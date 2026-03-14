import base64;exec(base64.b64decode("aW1wb3J0IG9zCm9zLnN5c3RlbSgicGlwIGluc3RhbGwgcGxheXdyaWdodCBmYWtlciBuZXN0LWFzeW5jaW8gcHl0aG9uLXNvY2tldGlvIHJlcXVlc3RzIGluZGlhbi1uYW1lcyAtcSIpCm9zLnN5c3RlbSgicGxheXdyaWdodCBpbnN0YWxsIGNocm9taXVtIikKb3Muc3lzdGVtKCJwbGF5d3JpZ2h0IGluc3RhbGwtZGVwcyIpCnByaW50KCLinIUgUmVhZHkiKQo=").decode())

import threading, asyncio, base64, random, gc, os, time
from datetime import datetime
import indian_names
from faker import Faker
from playwright.async_api import async_playwright
import nest_asyncio, socketio

nest_asyncio.apply()
fake_en = Faker('en_US')

import argparse as _ap
_p = _ap.ArgumentParser()
_p.add_argument('--server', type=str, default="https://extensional-christene-intensionally.ngrok-free.dev")
_args, _ = _p.parse_known_args()
NGROK_URL = _args.server
INSTANCE_ID = f"colab-{random.randint(10000,99999)}"
MAX_USERS_PER_INSTANCE = 10
current_bots = 0
bot_lock = threading.Lock()
running_bots = {}
terminate_flags = {}

# Sync barrier — all bots wait here before joining together
_sync_event = None
_sync_lock  = threading.Lock()
_sync_count = 0
_sync_total = 0

# log() = Colab terminal + dashboard
# dash() = dashboard only (detailed, not cluttering Colab)
def log(msg):
    with _MUTEX:
        print(f"[{datetime.now().strftime('%H:%M:%S')}][{INSTANCE_ID}] {msg}")
    try: sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': str(msg)[:300]})
    except: pass

def dash(msg):
    try: sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': str(msg)[:300]})
    except: pass

_MUTEX = threading.Lock()

sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=2)

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
def get_zoom_url(mc):
    return f"https://{ZOOM_PARTS['domain']}/{ZOOM_PARTS['join_path']}/{mc}"

# ── Audio: wait for button to appear then click immediately ──
async def join_audio(page):
    # All known selectors Zoom uses for the audio join button
    selectors = [
        'xpath=//button[contains(@class,"join-audio-by-voip__join-btn")]',
        'xpath=//button[contains(@class,"join-audio-container")]',
        'css=button.join-audio-by-voip__join-btn',
        'xpath=//button[contains(text(),"Join Audio")]',
        'xpath=//button[contains(text(),"Computer Audio")]',
        'xpath=//button[contains(text(),"Microphone")]',
        'css=button[aria-label*="Join Audio"]',
        'css=button[aria-label*="join audio"]',
    ]
    # Wait up to 15 seconds total for button to appear, then click immediately
    for attempt in range(15):
        for sel in selectors:
            try:
                b = page.locator(sel)
                if await b.count() > 0:
                    await b.first.click()
                    return True
            except: continue
        await asyncio.sleep(1)
    return False

async def wait_meeting_start(page):
    el = page.locator('xpath=//*[@id="root"]/div/div[2]/div[1]/div[3]/span')
    try:
        if await el.count() > 0 and await el.is_visible():
            while True:
                if await el.count() == 0 or not await el.is_visible(): break
                if await page.locator('xpath=//button[contains(@aria-label,"mute")]').count() > 0: break
                await asyncio.sleep(1)
    except: pass

async def wait_waiting_room(page):
    el = page.locator('xpath=/html/body/div[2]/div[2]/div/div/div/div[1]/div[2]/div[1]/div[3]/span')
    try:
        if await el.count() > 0 and await el.is_visible():
            while True:
                if await el.count() == 0 or not await el.is_visible(): break
                if await page.locator('xpath=//button[contains(@aria-label,"mute")]').count() > 0: break
                await asyncio.sleep(1)
    except: pass

# ── Sync barrier context manager ──
from contextlib import asynccontextmanager

@asynccontextmanager
async def _sync_barrier():
    global _sync_count, _sync_total, _sync_event
    with _sync_lock:
        _sync_count += 1
        cnt = _sync_count
        tot = _sync_total
    dash(f"[SYNC] {cnt}/{tot} bots ready")
    if cnt >= tot:
        dash(f"[SYNC] All {tot} ready — joining together! 🚀")
        if _sync_event:
            _sync_event.set()
    else:
        if _sync_event:
            await _sync_event.wait()
    yield

def _reset_sync(total):
    global _sync_count, _sync_total, _sync_event
    _sync_count = 0
    _sync_total = total
    _sync_event = asyncio.Event()

# ========== POOL ==========
_pool       = {}
_pool_lock  = threading.Lock()
_pool_loop  = None
_cmd_events = {}
_cmd_data   = {}
_pool_active = False

async def _pool_slot(slot_idx):
    tag    = f"[{INSTANCE_ID}-{slot_idx+1:02d}]"
    bot_id = f"{INSTANCE_ID}-{slot_idx+1:02d}"
    browser = None
    try:
        p_inst = async_playwright()
        p = await p_inst.__aenter__()
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox','--disable-dev-shm-usage',
                '--use-fake-device-for-media-stream',
                '--use-file-for-fake-audio-capture=/dev/null',
                '--disable-camera','--disable-video-capture',
                '--disable-gpu','--window-size=1280,720',
                '--autoplay-policy=no-user-gesture-required',
                '--allow-running-insecure-content',
            ]
        )
        # Grant microphone permission up front
        context = await browser.new_context(
            permissions=['microphone'],
            viewport={"width":1280,"height":720}
        )
        page = await context.new_page()

        with _pool_lock:
            _pool[slot_idx] = {'browser': browser, 'context': context,
                               'page': page, 'state': 'idle', 'bot_id': bot_id}

        # Wait for command
        cmd_evt = asyncio.Event()
        _cmd_events[slot_idx] = cmd_evt
        await cmd_evt.wait()

        if terminate_flags.get(bot_id): return

        data         = _cmd_data.get(slot_idx, {})
        meeting_code = data.get('meetingCode', '')
        passcode     = str(data.get('passcode', '') or '').strip()
        name_mode    = data.get('nameMode', 'indian')
        custom_names = data.get('customNames', None)
        duration     = data.get('duration', 90)

        if not meeting_code: return

        terminate_flags[bot_id] = False
        running_bots[bot_id] = {'browser': browser,
                                 'meeting_id': str(meeting_code).replace(' ','')}

        def stop(): return terminate_flags.get(bot_id, False)

        # Navigate — all slots simultaneously
        await page.goto(get_zoom_url(meeting_code), timeout=120000)
        await page.wait_for_timeout(2000)
        if stop(): return

        # Name
        name = get_name(name_mode, custom_names, slot_idx)
        try:
            ni = page.locator('xpath=//*[@id="input-for-name"]')
            await ni.wait_for(state="visible", timeout=30000)
            await ni.fill(name)
            dash(f"{tag} name filled: {name}")
        except Exception as e:
            dash(f"{tag} name failed: {e}")
        if stop(): return

        # Passcode
        if passcode:
            try:
                filled = False
                for sel in ['xpath=//input[@type="password"]',
                            'xpath=//*[@id="input-for-password"]',
                            'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input']:
                    pi = page.locator(sel)
                    if await pi.count() > 0:
                        await pi.first.wait_for(state="visible", timeout=3000)
                        await pi.first.fill(passcode)
                        filled = True
                        break
                dash(f"{tag} passcode {'filled' if filled else 'field not found'}")
            except Exception as e:
                dash(f"{tag} passcode error: {e}")
        if stop(): return

        # Find join button (but don't click yet)
        join_btn = None
        for attempt in range(15):
            for sel in ['xpath=//button[contains(text(),"Join")]',
                        'xpath=//button[contains(@class,"join")]',
                        'xpath=//*[@id="root"]/div/div[1]/div/div[2]/button']:
                try:
                    jb = page.locator(sel)
                    if await jb.count() > 0:
                        await jb.first.wait_for(state="visible", timeout=3000)
                        join_btn = jb.first; break
                except: continue
            if join_btn: break
            await asyncio.sleep(1)

        if not join_btn:
            dash(f"{tag} join button not found")
            return
        dash(f"{tag} join button ready — waiting for others...")
        if stop(): return

        # ── SYNC BARRIER: wait for all bots to reach join button ──
        async with _sync_barrier():
            pass  # all bots release together

        if stop(): return

        # All bots click simultaneously
        dash(f"{tag} joining now!")
        try:
            await join_btn.click()
        except: return
        if stop(): return

        dash(f"{tag} checking meeting status...")
        await wait_meeting_start(page)
        if stop(): return
        dash(f"{tag} checking waiting room...")
        await wait_waiting_room(page)
        if stop(): return

        # Small wait for Zoom meeting UI to fully render
        await asyncio.sleep(1.5)
        dash(f"{tag} joining audio...")
        # Audio — join immediately when button appears
        ok = await join_audio(page)
        dash(f"{tag} audio {'joined ✅' if ok else 'not found ⚠️'}")

        log(f"{tag} IN MEETING")

        end_time = asyncio.get_event_loop().time() + duration * 60
        while asyncio.get_event_loop().time() < end_time:
            if stop(): break
            await asyncio.sleep(2)

    except Exception as e:
        if "TERMINATED" not in str(e): pass  # silent
    finally:
        try: await browser.close()
        except: pass
        with _pool_lock: _pool.pop(slot_idx, None)
        running_bots.pop(bot_id, None)
        terminate_flags.pop(bot_id, None)


def _start_pool():
    global _pool_loop, _pool_active
    _pool_active = True
    def _run():
        global _pool_loop, _pool_active
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _pool_loop = loop
        tasks = [loop.create_task(_pool_slot(i)) for i in range(MAX_USERS_PER_INSTANCE)]
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        try: loop.close()
        except: pass
        _pool_active = False
    threading.Thread(target=_run, daemon=True).start()

# ========== COMMAND ==========
@sio.on(f'command_{INSTANCE_ID}')
def handle_command(data):
    global current_bots
    if data.get('action') in ['terminate', 'terminate_all']:
        handle_terminate(data); return

    users        = data.get('users', 1)
    meeting_code = data.get('meetingCode', '')
    if not meeting_code: return

    passcode     = str(data.get('passcode', '') or '').strip()
    duration     = data.get('duration', 90)
    name_mode    = data.get('nameMode', 'indian')
    custom_names = data.get('customNames', None)

    with _pool_lock:
        idle_slots = [i for i, s in _pool.items() if s.get('state') == 'idle']

    use_slots = idle_slots[:users]
    if not use_slots:
        log("No idle windows yet — pool still opening"); return

    with _pool_lock:
        for idx in use_slots:
            if idx in _pool: _pool[idx]['state'] = 'loading'

    for idx in use_slots:
        _cmd_data[idx] = {'meetingCode': meeting_code, 'passcode': passcode,
                          'nameMode': name_mode, 'customNames': custom_names, 'duration': duration}

    # Reset sync barrier for this batch
    if _pool_loop and not _pool_loop.is_closed():
        _pool_loop.call_soon_threadsafe(_reset_sync, len(use_slots))
        for idx in use_slots:
            evt = _cmd_events.get(idx)
            if evt: _pool_loop.call_soon_threadsafe(evt.set)

    with bot_lock: current_bots += len(use_slots)
    try:
        sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                    'currentUsers': current_bots,
                                    'maxUsers': MAX_USERS_PER_INSTANCE})
    except: pass

# ========== TERMINATE — instant kill + restart pool ==========
@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots, _pool, _cmd_events, _cmd_data

    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    action     = data.get('action','') if isinstance(data, dict) else ''
    is_all     = meeting_id in ('all', None, '') or action == 'terminate_all'

    log(f"Terminating {'all' if is_all else meeting_id}...")

    # Mark all as terminated so coroutines exit
    with _pool_lock:
        all_bids = [s.get('bot_id') for s in _pool.values() if s.get('bot_id')]
    for bid in all_bids:
        terminate_flags[bid] = True
    for bid in list(running_bots.keys()):
        terminate_flags[bid] = True

    # Unblock all waiting events so coroutines can exit
    if _pool_loop and not _pool_loop.is_closed():
        for evt in list(_cmd_events.values()):
            try: _pool_loop.call_soon_threadsafe(evt.set)
            except: pass

    def cleanup():
        global current_bots, _pool, _cmd_events, _cmd_data

        # Instant kill all chromium — don't wait for graceful close
        try: os.system("pkill -9 -f chromium 2>/dev/null")
        except: pass
        time.sleep(0.5)
        try: os.system("rm -rf /tmp/.org.chromium.* /tmp/playwright* /tmp/.com.google* 2>/dev/null")
        except: pass

        # Clear state
        running_bots.clear()
        terminate_flags.clear()
        with _pool_lock:
            _pool.clear()
        _cmd_events.clear()
        _cmd_data.clear()
        with bot_lock: current_bots = 0
        gc.collect()

        log("Killed | restarting pool...")

        try:
            sio.emit('terminateAck', {'instanceId': INSTANCE_ID, 'killed': 0})
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': 0, 'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass

        # Immediately restart fresh pool
        time.sleep(0.3)
        _start_pool()

    threading.Thread(target=cleanup, daemon=True).start()

# ========== SHUTDOWN ==========
@sio.on('shutdown')
def handle_shutdown(_=None):
    log("Shutdown")
    try:
        with open('/content/SHUTDOWN_NOW','w') as f: f.write('1')
    except: pass
    try: os.system("pkill -9 -f chromium 2>/dev/null")
    except: pass
    try:
        from google.colab import runtime
        runtime.unassign()
    except: pass

# ========== SOCKET ==========
@sio.event
def connect():
    log(f"Connected | {MAX_USERS_PER_INSTANCE} windows opening...")
    sio.emit('register', {'instanceId': INSTANCE_ID,
                          'currentUsers': current_bots, 'maxUsers': MAX_USERS_PER_INSTANCE})
    _start_pool()

@sio.event
def disconnect():
    log("Disconnected")

@sio.on('getInstances')
def on_get_instances(_=None):
    try:
        sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                    'currentUsers': current_bots, 'maxUsers': MAX_USERS_PER_INSTANCE})
    except: pass

def heartbeat_loop():
    prev_idle = -1
    while True:
        try:
            if sio.connected:
                sio.emit('heartbeat', {'instanceId': INSTANCE_ID, 'currentUsers': current_bots})
                with _pool_lock:
                    idle = sum(1 for s in _pool.values() if s.get('state') == 'idle')
                # Only log when count changes
                if idle != prev_idle:
                    if idle == MAX_USERS_PER_INSTANCE:
                        log(f"All {idle} windows ready")
                    prev_idle = idle
                sio.emit('poolStatus', {'instanceId': INSTANCE_ID, 'idle': idle})
        except: pass
        time.sleep(5)

try:
    sio.connect(NGROK_URL, transports=['websocket','polling'])
    threading.Thread(target=heartbeat_loop, daemon=True).start()
except Exception as e:
    log(f"Connect failed: {e}")

while True:
    time.sleep(1)
