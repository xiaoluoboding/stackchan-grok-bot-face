#!/usr/bin/env python3
"""USB verification with the normal bridge stopped. Leaves white-eyes selected.

Snapshots are captured from the running device LVGL scene, not a browser mockup.
This checks the actual firmware renderer, not the physical LCD panel or ASR.
"""
import argparse
import base64
from pathlib import Path
import time
import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default='/dev/cu.usbmodem1101')
    parser.add_argument('--skin', choices=['white-eyes', 'original'], default='white-eyes')
    parser.add_argument('--speech', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('build/white-eyes-device'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with serial.Serial(args.port, 115200, timeout=.2, write_timeout=3, exclusive=True) as device, (args.output/'serial.log').open('a') as log:
        def receive(until, timeout=25, repeat=None):
            end = time.monotonic()+timeout
            retry = time.monotonic()+3
            while time.monotonic()<end:
                if repeat and time.monotonic() >= retry:
                    device.write((repeat+'\n').encode()); device.flush(); retry = time.monotonic()+3
                line = device.readline().decode(errors='replace').strip()
                if not line:
                    continue
                log.write(line+'\n'); log.flush()
                if any(x in line for x in ['Avatar', 'avatar', 'Using ', 'StateMachine', 'Speech audio delivered', 'USB chat', 'Guru Meditation', 'watchdog']):
                    if 'SNAPSHOT_DATA' not in line:
                        print(line, flush=True)
                if args.speech and ('USB chat channel failed' in line or 'USB chat unavailable or busy' in line):
                    raise RuntimeError('Voice service unavailable: '+line)
                if until(line):
                    return line
            raise TimeoutError('Device verification timed out')
        def send(command):
            device.write((command+'\n').encode()); device.flush()
        send('STACKCHAN_AVATAR status')
        receive(lambda line:'Avatar preference:' in line, 40, 'STACKCHAN_AVATAR status')
        send('STACKCHAN_AVATAR '+args.skin)
        receive(lambda line:f'Avatar command: {args.skin} OK' in line)
        # Allow the first decoded frames to replace the previous skin.
        # The LVGL update task decodes one pending skin frame even when TLS
        # pauses playback. Let that task draw before requesting the snapshot.
        time.sleep(.3)
        chunks=[]
        send('STACKCHAN_AVATAR snapshot')
        def collect(line):
            if 'STACKCHAN_AVATAR_SNAPSHOT_ERROR' in line:
                raise RuntimeError(line)
            prefix='STACKCHAN_AVATAR_SNAPSHOT_DATA '
            if line.startswith(prefix):
                chunks.append(base64.b64decode(line[len(prefix):], validate=True))
            return line=='STACKCHAN_AVATAR_SNAPSHOT_END'
        receive(collect)
        data=b''.join(chunks)
        if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            raise RuntimeError('Incomplete JPEG capture')
        target=args.output/(args.skin+'.jpg'); target.write_bytes(data)
        print('DEVICE_SNAPSHOT',target,len(data),flush=True)
        if args.speech:
            send('STACKCHAN_CHAT "你好"')
            receive(lambda line:'Speech audio delivered to speaker' in line, 40)
            receive(lambda line:'State: speaking -> idle' in line or 'StateMachine: speaking -> idle' in line, 40)
            print('Speech path returned to idle',flush=True)


if __name__=='__main__':
    main()
