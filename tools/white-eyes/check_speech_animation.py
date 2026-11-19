#!/usr/bin/env python3
"""Measure device animation cadence during a repeatable spoken response.
Stop the normal USB bridge first. Saves full local log and a compact summary.
"""
import argparse,json,re,statistics,time
from pathlib import Path
import serial
p=argparse.ArgumentParser()
p.add_argument('--label',required=True)
p.add_argument('--prompt',default='讲个三十秒的故事')
p.add_argument('--port',default='/dev/cu.usbmodem1101')
a=p.parse_args()
out=Path('build/speech-animation');out.mkdir(parents=True,exist_ok=True)
frames=[];decodes=[];audio=False;started=None;ended=None;watchdogs=0;previous_ms=None
with serial.Serial(a.port,115200,timeout=.2,write_timeout=3,exclusive=True) as device,(out/(a.label+'.log')).open('w') as log:
 device.write(('STACKCHAN_CHAT '+json.dumps(a.prompt,ensure_ascii=False)+'\n').encode());device.flush()
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  text=device.readline().decode(errors='replace').strip()
  if not text:continue
  log.write(text+'\n');log.flush()
  if 'USB chat unavailable or busy' in text and started is None:
   time.sleep(2)
   device.write(('STACKCHAN_CHAT '+json.dumps(a.prompt,ensure_ascii=False)+'\n').encode());device.flush()
   continue
  if any(s in text for s in ['USB chat channel failed','assert failed','stack overflow']): raise RuntimeError(text)
  if 'task_wdt:' in text and 'got triggered' in text:watchdogs+=1
  stamp=re.search(r'\((\d+)\)',text)
  now=int(stamp[1]) if stamp else None
  if 'State:' in text and '-> speaking' in text and started is None:
   started=now;previous_ms=None
  if started is not None:
   if 'Speech audio delivered to speaker' in text:audio=True
   match=re.search(r'fps=(\d+).*decode=(\d+)us',text)
   if match and now is not None:
    if previous_ms is not None and now>previous_ms:frames.append(int(match[1])*1000/(now-previous_ms));decodes.append(int(match[2]))
    previous_ms=now
    print(text,flush=True)
   if 'State: speaking -> idle' in text:
    ended=now;break
 if not audio or ended is None or not frames:raise RuntimeError('No complete speech/animation measurement')
 result={'label':a.label,'prompt':a.prompt,'speech_ms':ended-started,'samples':len(frames),'median_fps':round(statistics.median(frames),2),'min_fps':round(min(frames),2),'median_decode_us':round(statistics.median(decodes)),'watchdog_events':watchdogs,'audio_delivered':audio,'returned_idle':True}
 (out/(a.label+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
