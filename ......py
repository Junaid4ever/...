import base64;exec(base64.b64decode("aW1wb3J0IG9zCm9zLnN5c3RlbSgicGlwIGluc3RhbGwgdXZsb29wIHBsYXl3cmlnaHQgZmFrZXIgbmVzdC1hc3luY2lvIHB5dGhvbi1zb2NrZXRpbyByZXF1ZXN0cyBpbmRpYW4tbmFtZXMgLXEgPi9kZXYvbnVsbCAyPiYxIikKb3Muc3lzdGVtKCJwbGF5d3JpZ2h0IGluc3RhbGwgY2hyb21pdW0gPi9kZXYvbnVsbCAyPiYxIikKb3Muc3lzdGVtKCJwbGF5d3JpZ2h0IGluc3RhbGwtZGVwcyA+L2Rldi9udWxsIDI+JjEiKQpwcmludCgn4pyFIFJlYWR5JykK").decode())

import threading, asyncio, base64, random, gc, os, time
from datetime import datetime
import indian_names
from faker import Faker
from playwright.async_api import async_playwright
import nest_asyncio, socketio

try:
    import uvloop
    uvloop.install()
    print("✓ uvloop active")
except (ImportError, NotImplementedError):
    print("uvloop not available, using default asyncio")

nest_asyncio.apply()
fake_en = Faker('en_US')

# ========== CONFIG ==========
import argparse as _ap
_parser = _ap.ArgumentParser()
_parser.add_argument('--server', type=str, default="https://piano4ever.ngrok.app")
_args, _ = _parser.parse_known_args()
NGROK_URL = _args.server
INSTANCE_ID = f"colab-{int(time.time()*1000)%100000}"
MAX_USERS_PER_INSTANCE = 10
current_bots = 0
bot_lock = threading.Lock()
running_bots = {}
terminate_flags = {}
PAGE_LOAD_SEM = None

# ── Persistent event loop ──
try:
    _bot_loop = uvloop.new_event_loop()
except (NameError, NotImplementedError):
    _bot_loop = asyncio.new_event_loop()
threading.Thread(target=_bot_loop.run_forever, daemon=True).start()

print(f"[{datetime.now().strftime('%H:%M:%S')}] ID={INSTANCE_ID} | Max={MAX_USERS_PER_INSTANCE}")

sio = socketio.Client(reconnection=True)
MUTEX = threading.Lock()

def sync_print(msg):
    with MUTEX:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    try:
        msg_str = str(msg)
        skip = ['--disable','--enable','chromium','libatk','shared lib','temporary dir','pid=','chrome-headless']
        if any(k in msg_str.lower() for k in skip): return
        sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': msg_str[:300],
                            'ts': datetime.now().strftime('%H:%M:%S')})
    except: pass

def get_name(mode="indian", custom_list=None, index=0):
    if mode == "custom" and custom_list:
        return custom_list[index % len(custom_list)]
    elif mode == "english":
        return fake_en.name()
    return indian_names.get_full_name(gender=random.choice(['male','female']))

ZOOM_PARTS = {
    'domain': base64.b64decode('em9vbS51cw==').decode(),
    'join_path': base64.b64decode('d2Mvam9pbg==').decode()
}
def get_zoom_url(meeting_code):
    return f"https://{ZOOM_PARTS['domain']}/{ZOOM_PARTS['join_path']}/{meeting_code}"

READY_TO_JOIN = None
BOTS_READY = 0
BOTS_TOTAL = 0
BOTS_FAILED = 0
BOTS_LOCK = asyncio.Lock()

async def wait_for_all_bots():
    global BOTS_READY
    async with BOTS_LOCK:
        BOTS_READY += 1
        ready, total, failed = BOTS_READY, BOTS_TOTAL, BOTS_FAILED
    sync_print(f"[SYNC] {ready}/{total} bots ready (failed: {failed})")
    if ready + failed >= total:
        READY_TO_JOIN.set()
        sync_print("[SYNC] All bots ready! Joining together...")
    await READY_TO_JOIN.wait()

async def _unblock_sync():
    if READY_TO_JOIN: READY_TO_JOIN.set()

async def join_audio_computer(page, tag):
    try:
        await asyncio.sleep(1)
        for sel in [
            'xpath=//button[contains(text(),"Join Audio")]',
            'xpath=//button[contains(text(),"Computer Audio")]',
            'xpath=//button[contains(@class,"join-audio")]',
            'css=button[aria-label*="Join Audio"]',
        ]:
            try:
                btn = page.locator(sel)
                if await btn.count() > 0:
                    await btn.first.wait_for(state="visible", timeout=4000)
                    await btn.first.click()
                    sync_print(f"{tag} audio joined")
                    return True
            except: continue
        muted = page.locator('xpath=//button[contains(@aria-label,"mute") or contains(@aria-label,"Mute")]')
        if await muted.count() > 0:
            sync_print(f"{tag} already has audio")
            return True
    except Exception as e:
        sync_print(f"{tag} audio skip: {e}")
    return False

async def wait_for_meeting_to_start(page, tag):
    try:
        el = page.locator('xpath=//*[@id="root"]/div/div[2]/div[1]/div[3]/span')
        if await el.count() > 0 and await el.is_visible():
            sync_print(f"{tag} waiting for host...")
            while True:
                try:
                    if await el.count() == 0 or not await el.is_visible(): break
                    for ind in ['xpath=//button[contains(@aria-label,"mute")]',
                                'xpath=//button[contains(@aria-label,"Leave")]']:
                        if await page.locator(ind).count() > 0: return True
                    await asyncio.sleep(2)
                except: await asyncio.sleep(2)
    except: pass
    return True

async def wait_for_waiting_room(page, tag):
    try:
        el = page.locator('xpath=/html/body/div[2]/div[2]/div/div/div/div[1]/div[2]/div[1]/div[3]/span')
        if await el.count() > 0 and await el.is_visible():
            sync_print(f"{tag} IN WAITING ROOM...")
            while True:
                try:
                    if await el.count() == 0 or not await el.is_visible(): break
                    for ind in ['xpath=//button[contains(@aria-label,"mute")]',
                                'xpath=//button[contains(@aria-label,"Leave")]']:
                        if await page.locator(ind).count() > 0: return True
                    await asyncio.sleep(2)
                except: await asyncio.sleep(2)
    except: pass
    return True

async def start(tag, wait_time, meetingcode, passcode, headless,
                bot_index=0, bot_id=None, name_mode="indian", custom_names=None):
    global BOTS_FAILED

    if bot_id: terminate_flags[bot_id] = False
    def stop(): return bot_id and terminate_flags.get(bot_id, False)

    await PAGE_LOAD_SEM.acquire()
    browser = context = page = None
    sem_released = False

    try:
        if stop():
            PAGE_LOAD_SEM.release()
            return

        p = await async_playwright().__aenter__()
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox','--disable-dev-shm-usage',
                '--use-fake-device-for-media-stream',
                '--use-file-for-fake-audio-capture=/dev/null',
                '--mute-audio','--disable-camera','--disable-video-capture',
                '--disable-gpu','--window-size=1280,720',
                '--incognito','--no-first-run','--disable-default-apps',
            ]
        )
        if bot_id:
            running_bots[bot_id] = {'browser': browser, 'meeting_id': str(meetingcode).replace(' ','')}

        context = await browser.new_context(permissions=[], viewport={"width":1280,"height":720})
        async def _block(dialog): await dialog.dismiss()
        page = await context.new_page()
        page.on("dialog", _block)

        await page.goto(get_zoom_url(meetingcode), timeout=90000)
        await page.wait_for_timeout(2000)

        # NAME
        try:
            ni = page.locator('xpath=//*[@id="input-for-name"]')
            await ni.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(0.3)
            user_name = get_name(name_mode, custom_names, bot_index)
            await ni.fill(user_name)
            sync_print(f"{tag} name filled: {user_name}")
        except Exception as e:
            sync_print(f"{tag} name failed: {e}")
            async with BOTS_LOCK: BOTS_FAILED += 1
            PAGE_LOAD_SEM.release()
            try: await browser.close()
            except: pass
            running_bots.pop(bot_id, None)
            terminate_flags.pop(bot_id, None)
            return

        PAGE_LOAD_SEM.release()
        sem_released = True
        if stop(): raise Exception("TERMINATED")

        # PASSCODE
        if passcode:
            sync_print(f"{tag} entering passcode: {passcode}")
            pass_input = None
            try:
                for sel in [
                    'xpath=/html/body/div[2]/div[1]/div/div[1]/div/div[2]/div[2]/div/input',
                    'xpath=//*[@id="input-for-password"]',
                    'xpath=//input[@type="password"]',
                    'xpath=//input[contains(@placeholder,"code")]',
                    'xpath=//input[contains(@aria-label,"code")]',
                    'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input',
                ]:
                    try:
                        pi = page.locator(sel)
                        if await pi.count() > 0:
                            await pi.first.wait_for(state="visible", timeout=8000)
                            pass_input = pi.first
                            break
                    except: continue
                if pass_input:
                    await asyncio.sleep(0.3)
                    await pass_input.fill(passcode)
                    sync_print(f"{tag} passcode filled: {passcode}")
                else:
                    sync_print(f"{tag} passcode field not found — screenshot")
                    try:
                        ss = await page.screenshot(type='jpeg', quality=60, full_page=False)
                        sio.emit('botScreenshot', {
                            'instanceId': INSTANCE_ID, 'tag': tag,
                            'meetingId': str(meetingcode).replace(' ',''),
                            'screenshot': base64.b64encode(ss).decode(),
                            'reason': 'passcode field not found'
                        })
                    except: pass
            except Exception as e:
                sync_print(f"{tag} passcode error: {e}")

        # SYNC + JOIN
        await wait_for_all_bots()
        if stop(): raise Exception("TERMINATED")

        join_btn = None
        for sel in [
            'xpath=//button[contains(text(),"Join")]',
            'xpath=//button[contains(@class,"join")]',
            'xpath=//*[@id="root"]/div/div[1]/div/div[2]/button'
        ]:
            try:
                jb = page.locator(sel)
                if await jb.count() > 0:
                    await jb.first.wait_for(state="visible", timeout=5000)
                    join_btn = jb.first
                    break
            except: continue

        if join_btn:
            await asyncio.sleep(random.uniform(0.2, 0.6))
            await join_btn.click()
            sync_print(f"{tag} join clicked")
        else:
            sync_print(f"{tag} join button not found")
            try: await browser.close()
            except: pass
            return

        await wait_for_meeting_to_start(page, tag)
        await wait_for_waiting_room(page, tag)
        await join_audio_computer(page, tag)

        sync_print(f"{tag} IN MEETING — staying {wait_time//60}min")
        elapsed = 0
        while elapsed < wait_time:
            if stop():
                sync_print(f"{tag} terminated")
                break
            await asyncio.sleep(2)
            elapsed += 2

    except Exception as e:
        if "TERMINATED" not in str(e):
            sync_print(f"{tag} error: {e}")
        async with BOTS_LOCK: BOTS_FAILED += 1
        if not sem_released:
            try: PAGE_LOAD_SEM.release()
            except: pass
    finally:
        for obj in [page, context, browser]:
            if obj:
                try: await obj.close()
                except: pass
        gc.collect()
        running_bots.pop(bot_id, None)
        terminate_flags.pop(bot_id, None)
        sync_print(f"{tag} done")

@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots
    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    if meeting_id: meeting_id = str(meeting_id).replace(' ','')
    if meeting_id in ('all', None, ''):
        targets = list(running_bots.items())
    else:
        targets = [(bid, b) for bid, b in list(running_bots.items())
                   if str(b.get('meeting_id','')).replace(' ','') == meeting_id]

    killed = len(targets)
    sync_print(f"Terminating {killed} bots...")
    for bot_id, _ in targets:
        terminate_flags[bot_id] = True
    try:
        asyncio.run_coroutine_threadsafe(_unblock_sync(), _bot_loop)
    except: pass

    def cleanup():
        global current_bots
        futures = []
        for bot_id, info in targets:
            try:
                br = info.get('browser')
                if br:
                    futures.append(asyncio.run_coroutine_threadsafe(br.close(), _bot_loop))
            except: pass
        for f in futures:
            try: f.result(timeout=5)
            except: pass
        time.sleep(0.5)
        for bid, _ in targets:
            running_bots.pop(bid, None)
            terminate_flags.pop(bid, None)
        with bot_lock:
            current_bots = len(running_bots)
        if not running_bots:
            try: os.system("pkill -9 -f chromium 2>/dev/null")
            except: pass
        gc.collect()
        sync_print(f"Freed {killed} | active={len(running_bots)} | slots={MAX_USERS_PER_INSTANCE - current_bots} free | READY")
        try:
            sio.emit('terminateAck', {'instanceId': INSTANCE_ID, 'killed': killed})
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': current_bots,
                                        'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass

    threading.Thread(target=cleanup, daemon=True).start()

@sio.on(f'command_{INSTANCE_ID}')
def handle_command(data):
    global current_bots, BOTS_TOTAL, BOTS_READY, BOTS_FAILED, READY_TO_JOIN, PAGE_LOAD_SEM

    if data.get('action') in ['terminate','terminate_all']:
        handle_terminate(data); return

    users        = data.get('users', 1)
    meeting_code = data.get('meetingCode')
    if not meeting_code:
        sync_print("ERROR: meetingCode missing"); return

    passcode     = data.get('passcode','')
    duration     = data.get('duration', 90)
    headless     = data.get('headless', True)
    name_mode    = data.get('nameMode','indian')
    custom_names = data.get('customNames', None)

    sync_print(f"Starting {users} bots | meeting={meeting_code} | mode={name_mode}")

    with bot_lock:
        if current_bots + users > MAX_USERS_PER_INSTANCE:
            sync_print(f"Capacity full ({current_bots}/{MAX_USERS_PER_INSTANCE})")
            return
        current_bots += users

    BOTS_TOTAL    = users
    BOTS_READY    = 0
    BOTS_FAILED   = 0
    READY_TO_JOIN = asyncio.Event()
    PAGE_LOAD_SEM = asyncio.Semaphore(5)

    for i in range(users):
        asyncio.run_coroutine_threadsafe(
            start(f"[{INSTANCE_ID}-{i+1:02d}]", duration * 60, meeting_code,
                  passcode, headless, bot_index=i,
                  bot_id=f"{INSTANCE_ID}-{i+1:02d}-{meeting_code}",
                  name_mode=name_mode, custom_names=custom_names),
            _bot_loop
        )

_shutdown_triggered = False
_SHOULD_UNASSIGN = False

@sio.event
def connect():
    sync_print("Connected to server")
    sio.emit('register', {'instanceId': INSTANCE_ID,
                          'currentUsers': current_bots, 'maxUsers': MAX_USERS_PER_INSTANCE})

@sio.event
def disconnect():
    sync_print("Disconnected")

@sio.on('shutdown')
def handle_shutdown(_=None):
    global _shutdown_triggered
    if _shutdown_triggered: return
    _shutdown_triggered = True
    sync_print("Shutdown — unassigning Colab x5...")
    def _do():
        try:
            with open('/content/SHUTDOWN_NOW','w') as f: f.write('1')
        except: pass
        for i in range(5):
            try:
                from google.colab import runtime
                runtime.unassign()
                sync_print(f"Unassign {i+1}/5")
            except Exception as e:
                sync_print(f"Unassign {i+1} err: {e}")
            time.sleep(0.4)
        try: os._exit(0)
        except: pass
    threading.Thread(target=_do, daemon=True).start()

@sio.on('doUnassign')
def handle_unassign(data=None):
    global _SHOULD_UNASSIGN
    _SHOULD_UNASSIGN = True

@sio.on('getInstances')
def on_get_instances(_=None):
    try:
        sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                    'currentUsers': current_bots,
                                    'maxUsers': MAX_USERS_PER_INSTANCE})
    except: pass

def heartbeat_loop():
    while True:
        try:
            if sio.connected:
                sio.emit('heartbeat', {'instanceId': INSTANCE_ID, 'currentUsers': current_bots})
        except: pass
        time.sleep(5)

try:
    sio.connect(NGROK_URL, transports=['websocket','polling'])
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    sync_print("Ready")
except Exception as e:
    sync_print(f"Connect failed: {e}")

while True:
    time.sleep(1)
    if _SHOULD_UNASSIGN:
        try:
            with open('/content/unassign_trigger.txt','w') as f: f.write('1')
        except: pass
        break
