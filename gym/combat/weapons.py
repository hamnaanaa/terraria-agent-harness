"""Run the live brain with different weapon mechanics in the disposable Gym."""
import argparse
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/'gym/live-brain'), str(ROOT/'live-brain'), str(ROOT)]
import collect
from cases import WEAPONS
import gym_api as gym
from loop import Loop

# Item names select inventory only. All attack parameters come from AgentIO.
LOADOUTS = {
    'swing': [('Gold Broadsword',1,0)], 'shortsword': [('Copper Shortsword',1,0)],
    'bow': [('Gold Bow',1,0),('Wooden Arrow',200,54)],
    'gun': [('The Undertaker',1,0),('Musket Ball',200,54)],
    'knife': [('Throwing Knife',100,0)], 'shuriken': [('Shuriken',100,0)],
    'magic': [('Amethyst Staff',1,0)], 'magic_variant': [('Sapphire Staff',1,0)],
    'guided_magic': [('Magic Missile',1,0)], 'laser_bolt': [('Space Gun',1,0)],
    'boomerang': [('Wooden Boomerang',1,0)], 'boomerang_variant': [('Enchanted Boomerang',1,0)],
    'grenade': [('Grenade',25,0)], 'spear': [('Spear',1,0)], 'spear_variant': [('Trident',1,0)],
    'whip': [('Leather Whip',1,0)], 'whip_variant': [('Snapthorn',1,0)],
    'yoyo': [('Wooden Yoyo',1,0)], 'yoyo_variant': [('Rally',1,0)],
    'flail': [("Ball O' Hurt",1,0)],
    'summon': [('Finch Staff',1,0),('Leather Whip',1,1)],
    'sentry': [('Queen Spider Staff',1,0),('Wooden Sword',1,1)],
    'mana_backup': [('Amethyst Staff',1,0),('Wooden Sword',1,1)],
    'throwing_backup': [('Throwing Knife',1,0),('Gold Broadsword',1,1)],
    'ammo_backup': [('Gold Bow',1,0),('Wooden Arrow',2,54),('Gold Broadsword',1,1)],
    'returning_airborne': [('Enchanted Boomerang',1,0)],
    'magic_airborne': [('Sapphire Staff',1,0)],
    'whip_group': [('Snapthorn',1,0)],
    'travel_mixed': [('Gold Bow',1,0),('Gold Broadsword',1,1),('Sapphire Staff',1,2),('Wooden Arrow',200,54)],
}

SETTINGS = {
    'mana_backup': {'mana_max':20},
    'throwing_backup': {'foes':[['Zombie',16,0,'flat']]},
    'returning_airborne': {'foes':[['DemonEye',10,-6,'flat']]},
    'magic_airborne': {'foes':[['CaveBat',10,-5,'flat']]},
    'whip_group': {'foes':[['BlueSlime',8,0,'flat'],['BlueSlime',-8,0,'flat'],['BlueSlime',12,0,'flat']]},
    'travel_mixed': {'action':'cross','foes':[['Zombie',8,0,'flat'],['BlueSlime',8,0,'flat'],['DemonEye',8,-6,'flat']]},
}


def run(name, number, folder):
    gym.in_gym()
    gym.gym(clear_projectiles=True)
    settings=SETTINGS.get(name,{})
    case=dict(name=name, phase='live', tier='early', weapon=name, action='defeat', repeat=1,
              foes=[['Zombie',8,0,'flat']])
    case.update({k:v for k,v in settings.items() if k!='mana_max'})
    initial=collect.setup(case, number)
    gym.gym(mana_max=settings.get('mana_max',80))
    initial['state']=gym.state(x0=initial['origin'][0],y0=initial['origin'][1],w=initial['size'][0],h=initial['size'][1])
    snapshots=[];rounds=[]
    class ObservedLoop(Loop):
        def read(self):
            state=super().read();snapshots.append(state);return state
    with gzip.open(folder/f'{number:02d}-{name}.jsonl.gz','wt',compresslevel=3) as stream:
        def record(row):
            stream.write(json.dumps(row,separators=(',',':'))+'\n')
            if row['type']=='round':rounds.append(row)
        record(dict(type='start',case=case,**initial))
        brain=ObservedLoop(world_name='Gym')
        preparation=None
        if name in ('summon','sentry'):
            staff=next(i for i in initial['state']['bag'] if i['slot']==0)
            goal=dict(kind='interact',operation='use',item=staff['name'],projectile=staff['shoot'],limit_ticks=180)
            preparation=brain.run(goal,record,episode=f'Weapons {number}/{len(LOADOUTS)}: summon a companion')
        if case['action']=='cross':
            left,top=initial['origin'];floor=initial['size'][2]
            goal=dict(kind='reach',area=[left+79,top+floor-4,left+86,top+floor-1],limit_ticks=900)
            title='travel past enemies with mixed weapons'
        else:
            enemy=next(e for e in initial['state']['enemies'] if e['i']==initial['spawned'][0])
            goal=dict(kind='defeat',enemy=enemy['name'],count=len(initial['spawned']),limit_ticks=900)
            title=f'{LOADOUTS[name][0][0]} against {len(initial["spawned"])} x {enemy["name"]}'
        if name.endswith('_backup'):title+='; use the backup when supplies run out'
        outcome=brain.run(goal,record,episode=f'Weapons {number}/{len(LOADOUTS)}: {title}')
        after=gym.state(w=90,h=50)
        record(dict(type='finish',state=after,result=outcome,preparation=preparation))
        for state in snapshots:record(dict(type='observation',state=state))
    before=initial['state']
    previous={p['id']:p for p in before['counters'].get('by_projectile',[])}
    projectiles=[dict(id=p['id'],name=p['name'],**{k:p[k]-previous.get(p['id'],{}).get(k,0)
                 for k in ('fired','hits','projectiles_hit','damage')}) for p in after['counters'].get('by_projectile',[])]
    projectiles=[p for p in projectiles if any(p[k] for k in ('fired','hits','damage'))]
    previous={p['id']:p for p in before['counters'].get('by_weapon',[])}
    weapons=[dict(id=p['id'],name=p['name'],**{k:p[k]-previous.get(p['id'],{}).get(k,0)
             for k in ('attacks','hits','damage')}) for p in after['counters'].get('by_weapon',[])]
    weapons=[p for p in weapons if p['attacks'] or p['hits']]
    checks={}
    if name.endswith('_backup'):
        primary,backup=LOADOUTS[name][0][0],LOADOUTS[name][-1][0]
        checks['used_primary']=any(p['name']==primary and p['attacks']>0 for p in weapons)
        checks['hit_with_backup']=any(p['name']==backup and p['hits']>0 for p in weapons)
        checks['resource_depleted']=(after['body']['mana']<5 if name=='mana_backup' else
            not any(i['name']==('Wooden Arrow' if name=='ammo_backup' else primary) for i in after['bag']))
    if goal['kind']=='defeat':
        checks['kill_count']=after['counters']['kills_by_name'].get(goal['enemy'],0)-before['counters']['kills_by_name'].get(goal['enemy'],0)==goal['count']
    if preparation is not None:checks['prepared']=preparation['status']=='done'
    def timing(phase):
        values=[s['end_ms']-s['start_ms'] for r in rounds for s in r['spans'] if s['phase']==phase]
        values.sort()
        return dict(median_ms=round(statistics.median(values),2),p95_ms=round(values[max(0,(len(values)*95+99)//100-1)],2)) if values else None
    return dict(name=name,number=number,weapon=LOADOUTS[name],status=outcome['status'],reason=outcome['reason'],
                ticks=outcome['ticks'],life=after['body']['life'],lost=before['body']['life']-after['body']['life'],
                dealt=after['counters']['damage_dealt']-before['counters']['damage_dealt'],
                fired=after['counters']['projectiles_fired']-before['counters']['projectiles_fired'],
                mana=after['body'].get('mana'),preparation=preparation,
                kills=after['counters']['kills_by_name'].get(goal.get('enemy'),0)-before['counters']['kills_by_name'].get(goal.get('enemy'),0),
                by_projectile=projectiles,by_weapon=weapons,checks=checks,
                predict=timing('fast.predict'),file=f'{number:02d}-{name}.jsonl.gz')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',action='append',help='Select a named mechanic; default is all.')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    gym.in_gym()
    folder=args.out or ROOT/'runs'/('weapons-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True,exist_ok=True)
    WEAPONS.update(LOADOUTS)
    selected=[(i,name) for i,name in enumerate(LOADOUTS,1) if not args.case or name in args.case]
    if not selected:parser.error('no matching cases')
    sources=list((ROOT/'live-brain').rglob('*.py'))+list((ROOT/'live-brain/rules').glob('*.json'))+list((ROOT/'mod/AgentIO').glob('*.cs'))+[Path(__file__)]
    hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in sources}
    (folder/'manifest.json').write_text(json.dumps(dict(loadouts=LOADOUTS,settings=SETTINGS,selected=selected,source_sha256=hashes),indent=2))
    results=[]
    for number,name in selected:
        result=run(name,number,folder);results.append(result)
        (folder/'summary.json').write_text(json.dumps(results,indent=2))
        print(f'{number}/{len(LOADOUTS)} {name}: {result["status"]}, {result["lost"]} HP lost, {result["ticks"]} ticks, checks={all(result["checks"].values())}',flush=True)
    assert all(sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in hashes.items()), 'code changed during the run'
    gym.gym(butcher=True,clear_projectiles=True,heal=True)
    gym.call('/overlay',{'layer':'weapon-results','labels':[{'at':[24,120],'screen':True,
             'text':f'Weapons: {sum(r["status"]=="done" for r in results)}/{len(results)} goals completed'}]})
    print(folder,flush=True)


if __name__=='__main__':main()
