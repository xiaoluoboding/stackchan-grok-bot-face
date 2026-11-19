#!/usr/bin/env python3
"""Verify the one-shot wake shy feedback on USB (stop the normal bridge first).

--microphone speaks the wake phrase through the Mac speaker and checks the
actual device wake event, in addition to a deterministic visual smoke test.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
import serial

parser = argparse.ArgumentParser()
parser.add_argument('--port', default='/dev/cu.usbmodem1101')
parser.add_argument('--microphone', action='store_true')
args = parser.parse_args()
out = Path('build/wake-shy'); out.mkdir(parents=True, exist_ok=True)
results = {}
with serial.Serial(args.port,115200,timeout=.03,write_timeout=3,exclusive=True) as device, (out/'serial.log').open('a') as log:
    def send(text):
        device.write((text+'\n').encode()); device.flush()
    def line():
        value=device.readline().decode(errors='replace').strip()
        if value:
            log.write(value+'\n'); log.flush()
            if any(k in value for k in ['Wake shy','Wake word detected','Avatar command','StateMachine','stack overflow','assert failed']):
                print(value,flush=True)
            if 'stack overflow' in value or 'assert failed' in value or 'Guru Meditation' in value:
                raise RuntimeError(value)
        return value
    def until(predicate, timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            text=line()
            if text and predicate(text): return text
        raise TimeoutError('No expected device event')
    send('STACKCHAN_AVATAR status')
    until(lambda text:'Avatar preference:' in text,40)
    for skin in ['original','white-eyes']:
        send('STACKCHAN_AVATAR '+skin)
        until(lambda text:f'Avatar command: {skin} OK' in text)
        send('STACKCHAN_AVATAR wake-shy')
        until(lambda text:'Wake shy started' in text)
        start=time.monotonic()
        # A duplicate trigger during playback must not extend or repeat it.
        send('STACKCHAN_AVATAR wake-shy')
        while time.monotonic()-start<.28:
            text=line()
            if 'Wake shy started' in text: raise RuntimeError('Duplicate heart trigger was not debounced')
        send('STACKCHAN_AVATAR snapshot')
        parts=[]
        def capture(text):
            if 'SNAPSHOT_ERROR' in text: raise RuntimeError(text)
            prefix='STACKCHAN_AVATAR_SNAPSHOT_DATA '
            if text.startswith(prefix): parts.append(base64.b64decode(text[len(prefix):],validate=True))
            return text=='STACKCHAN_AVATAR_SNAPSHOT_END'
        until(capture,5)
        data=b''.join(parts)
        if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'): raise RuntimeError('Incomplete JPEG')
        (out/(skin+'-shy.jpg')).write_bytes(data)
        until(lambda text:'Wake shy finished' in text,4)
        duration=time.monotonic()-start
        if not .9<duration<1.8: raise RuntimeError(f'Heart duration out of bounds: {duration}')
        results[skin]={'duration_seconds':round(duration,3),'duplicate_ignored':True,'snapshot_bytes':len(data)}
    if args.microphone:
        # Let the service finish its initial connection before exercising ASR.
        deadline=time.monotonic()+85
        idle=False
        while time.monotonic()<deadline:
            text=line()
            if ' -> idle' in text: idle=True;break
            # Already idle devices need not emit another transition. A full
            # second of flowing animation after startup is enough to try wake.
            if 'fps=' in text and time.monotonic()>deadline-80: break
        subprocess.Popen(['say','-v','Tingting','你好小智'])
        until(lambda text:'Wake word detected:' in text,15)
        until(lambda text:'Wake shy started' in text,3)
        until(lambda text:'Wake shy finished' in text,4)
        results['microphone_wake_word_triggered_shy']=True
    (out/'verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
