"""Common-enemy tests of the shared live brain, with optional window recordings."""
import argparse
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT/'live-brain'), str(ROOT/'gym/live-brain'), str(ROOT)]
import collect
import gym_api as gym
from loop import Loop

SWORD = [['Gold Broadsword',1,0]]
BOW = [['Gold Bow',1,0],['Wooden Arrow',300,54]]
MIXED = BOW + [['Gold Broadsword',1,1]]
MOLTEN = ['Molten Helmet','Molten Breastplate','Molten Greaves','Hermes Boots','Cloud in a Bottle']
FIRE_BOW = [['Molten Fury',1,0],["Jester's Arrow",300,54],['Gold Broadsword',1,1]]


def cases():
    rows = []
    solos = [('Zombie with sword','Zombie',SWORD,12,0,'flat'),
             ('Zombie with bow','Zombie',BOW,16,0,'flat'),
             ('Slime with sword','BlueSlime',SWORD,10,0,'flat'),
             ('Slime with bow','BlueSlime',BOW,14,0,'flat'),
             ('Flying eye with bow','DemonEye',BOW,14,-6,'flat'),
             ('Bat with boomerang','CaveBat',[['Enchanted Boomerang',1,0]],12,-5,'flat'),
             ('Vulture with sword','Vulture',SWORD,8,0,'flat'),
             ('Antlion shooting sand','Antlion',BOW,14,0,'ledge'),
             ('Hornet with bow','Hornet',BOW,14,-6,'flat'),
             ('Fire Imp with bow','FireImp',BOW,16,0,'flat'),
             ('Hellbat with Molten Fury','Hellbat',FIRE_BOW,14,-6,'flat'),
             ('Demon with Molten Fury','Demon',FIRE_BOW,16,-7,'flat')]
    for name,enemy,bag,dx,dy,terrain in solos:
        rows.append(dict(name=name,bag=bag,foes=[[enemy,dx,dy,terrain]],action='defeat'))
    rows += [dict(name='Three zombies from both sides',bag=MIXED,action='defeat',
                  foes=[['Zombie',12,0,'flat'],['Zombie',-12,0,'flat'],['Zombie',18,0,'flat']]),
             dict(name='Travel past zombie slime and eye',bag=MIXED,action='cross',
                  foes=[['Zombie',10,0,'flat'],['BlueSlime',15,0,'flat'],['DemonEye',20,-6,'flat']]),
             dict(name='Travel past Underworld enemies',bag=FIRE_BOW,action='cross',
                  foes=[['FireImp',10,0,'flat'],['Hellbat',18,-6,'flat'],['Demon',24,-7,'flat']]),
             dict(name='Survive among zombie slime and eye',bag=MIXED,action='maintain',
                  foes=[['Zombie',12,0,'flat'],['BlueSlime',-12,0,'flat'],['DemonEye',16,-6,'flat']])]
    rows += [dict(name='Hornet at range with sword',bag=SWORD,action='defeat',
                  foes=[['Hornet',30,-6,'flat']]),
             dict(name='Fire Imp at range with sword',bag=SWORD,action='defeat',
                  foes=[['FireImp',30,0,'flat']])]
    for row in rows:
        row.update(phase='live',tier='early',weapon='explicit loadout',repeat=1)
        if row['bag'] == FIRE_BOW:
            row.update(wear=MOLTEN,life_max=300)
    return rows


def run(case, number, folder, recorder, window, total):
    gym.gym(butcher=True, clear_projectiles=True)
    stem = f'{number:02d}'
    movie = folder/f'{stem}.mov'
    process = None
    snapshots, rows, end_rows = [], [], []
    if recorder:
        process = subprocess.Popen([str(recorder),str(window),'80',str(movie)],
                                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        for line in process.stdout:
            if line.strip() == 'recording':break
        else:raise RuntimeError('recorder did not start')
    try:
        initial = collect.setup(case,number)
        before = initial['state']
        left,top = initial['origin']; width,height,floor = initial['size']
        if case['action'] == 'cross':
            goal = dict(kind='reach',area=[left+79,top+floor-4,left+86,top+floor-1],limit_ticks=1800)
        elif case['action'] == 'maintain':
            goal = dict(kind='maintain',area=[left+10,top+2,left+width-11,top+floor-1],ticks=900,limit_ticks=1200)
        else:
            enemy = next(e for e in before['enemies'] if e['i']==initial['spawned'][0])
            goal = dict(kind='defeat',enemy=enemy['name'],count=len(initial['spawned']),limit_ticks=1800)
        goal['label'] = case['name']
        class RecordedLoop(Loop):
            def read(self):
                state = super().read(); snapshots.append(state); return state
        with gzip.open(folder/f'{stem}.jsonl.gz','xt',compresslevel=3) as stream:
            def record(row):
                stream.write(json.dumps(row,separators=(',',':'))+'\n')
                if row['type']=='round':rows.append(row)
                if row['type']=='round_end':end_rows.append(row)
            record(dict(type='start',case=case,**initial))
            outcome = RecordedLoop(world_name='Gym').run(goal,record,episode=f'{number}/{total}: {case["name"]}')
            after = gym.state(w=90,h=50)
            record(dict(type='finish',result=outcome,state=after))
            gym.gym(butcher=True,clear_projectiles=True)
            for state in snapshots:record(dict(type='observation',state=state))
        summary = collect.summarize(before,after,case,outcome,rows,[])
        summary.update(number=number,goal=goal,file=f'{stem}.jsonl.gz',movie=movie.name if recorder else None,
                       first_tick=before['tick'],last_tick=after['tick'],
                       observation_to_handoff_ticks=[r['decision']['forecast_start_tick']-r['tick']
                           for r in rows if 'forecast_start_tick' in r['decision']],
                       round_ms=[r['round_ms'] for r in end_rows])
        return summary
    finally:
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT); process.wait(timeout=15)
        gym.gym(butcher=True,clear_projectiles=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--case',type=int,action='append')
    parser.add_argument('--recorder',type=Path)
    parser.add_argument('--window',type=int)
    args = parser.parse_args()
    if args.recorder and not args.window:parser.error('--recorder requires --window')
    gym.in_gym()
    current = gym.state(w=2,h=2)
    if current['execution']['active'] or current['execution']['pending']:
        raise RuntimeError('another controller is active')
    folder = args.out or ROOT/'runs'/('observed-motion-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True,exist_ok=True)
    selected = [(n,c) for n,c in enumerate(cases(),1) if not args.case or n in args.case]
    sources = list((ROOT/'live-brain').rglob('*.py'))+[Path(__file__),Path(collect.__file__)]
    hashes = {str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in sources}
    (folder/'manifest.json').write_text(json.dumps(dict(cases=selected,sources=hashes,mode=current['mode']),indent=2))
    gym.gym(background=True,spawns=0)
    gym.call('/camera',{'follow':True,'zoom':.9,'hide_ui':False,'hide_cursor':True,'fullbright':1,'sun':0})
    results=[]
    for number,case in selected:
        result=run(case,number,folder,args.recorder,args.window,len(cases()))
        results.append(result)
        (folder/'summary.json').write_text(json.dumps(results,indent=2))
        print(json.dumps({k:result[k] for k in ['number','name','status','reason','ticks','life_end','damage_taken','damage_dealt','died']}),flush=True)
        assert all(sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in hashes.items()),'runtime changed during batch'
    gym.gym(butcher=True,clear_projectiles=True,heal=True)
    gym.call('/camera',{'fullbright':0,'sun':0})
    print(folder,flush=True)


if __name__=='__main__':main()
