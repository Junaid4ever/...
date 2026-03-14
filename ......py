import base64;exec(base64.b64decode("aW1wb3J0IG9zCm9zLnN5c3RlbSgicGlwIGluc3RhbGwgcGxheXdyaWdodCBmYWtlciBuZXN0LWFzeW5jaW8gcHl0aG9uLXNvY2tldGlvIHJlcXVlc3RzIGluZGlhbi1uYW1lcyAtcSIpCm9zLnN5c3RlbSgicGxheXdyaWdodCBpbnN0YWxsIGNocm9taXVtIikKb3Muc3lzdGVtKCJwbGF5d3JpZ2h0IGluc3RhbGwtZGVwcyIpCnByaW50KCLinIUgUmVhZHkiKQo=").decode())

import threading, asyncio, base64, random, gc, os, sys, time
from contextlib import asynccontextmanager
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
INSTANCE_ID = f"colab-{int(time.time()*1000)%100000}"
MAX_USERS_PER_INSTANCE = 10
current_bots = 0
bot_lock = threading.Lock()
running_bots = {}
terminate_flags = {}

print(f"[{datetime.now().strftime('%H:%M:%S')}] ID={INSTANCE_ID} | Max={MAX_USERS_PER_INSTANCE}")

sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=2)
MUTEX = threading.Lock()

def sync_print(msg):
    with MUTEX:
        print(f"[{datetime.now().strftime('%H:%M:%S')}][{INSTANCE_ID}] {msg}")
    try:
        sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': str(msg)[:300]})
    except: pass

def dash(msg):
    try: sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': str(msg)[:300]})
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
def get_zoom_url(mc):
    return f"https://{ZOOM_PARTS['domain']}/{ZOOM_PARTS['join_path']}/{mc}"

# ========== SYNC BARRIER ==========
_sync_event = None
_sync_lock  = threading.Lock()
_sync_count = 0
_sync_total = 0

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
        if _sync_event: _sync_event.set()
    else:
        if _sync_event: await _sync_event.wait()
    yield

def _reset_sync(total):
    global _sync_count, _sync_total, _sync_event
    _sync_count = 0
    _sync_total = total
    _sync_event = asyncio.Event()

# ========== AUDIO / MEETING HELPERS ==========
async def join_audio_computer(page, tag):
    try:
        for selector in [
            'xpath=//button[contains(text(), "Join Audio")]',
            'xpath=//button[contains(text(), "Computer Audio")]',
            'xpath=//button[contains(@class, "join-audio")]',
            'css=button[aria-label*="Join Audio"]',
            'xpath=//button[contains(text(), "Microphone")]'
        ]:
            try:
                audio_btn = page.locator(selector)
                if await audio_btn.count() > 0:
                    await audio_btn.first.wait_for(state="visible", timeout=5000)
                    await asyncio.sleep(1)
                    await audio_btn.first.click()
                    dash(f"{tag} audio joined ✅")
                    return True
            except: continue
        muted_btn = page.locator('xpath=//button[contains(@aria-label, "mute") or contains(@aria-label, "Mute")]')
        if await muted_btn.count() > 0:
            dash(f"{tag} audio already active ✅")
            return True
    except: pass
    dash(f"{tag} audio not found ⚠️")
    return False

async def wait_for_meeting_to_start(page, tag):
    waiting_xpath = 'xpath=//*[@id="root"]/div/div[2]/div[1]/div[3]/span'
    try:
        waiting_element = page.locator(waiting_xpath)
        if await waiting_element.count() > 0 and await waiting_element.is_visible():
            dash(f"{tag} waiting for host to start...")
            while True:
                try:
                    if await waiting_element.count() == 0 or not await waiting_element.is_visible():
                        break
                    for indicator in [
                        'xpath=//button[contains(@aria-label, "mute")]',
                        'xpath=//button[contains(text(), "Participants")]',
                        'xpath=//button[contains(@aria-label, "Leave")]'
                    ]:
                        if await page.locator(indicator).count() > 0: return True
                    await asyncio.sleep(2)
                except: await asyncio.sleep(2)
    except: pass
    return True

async def wait_for_waiting_room(page, tag):
    waiting_room_xpath = 'xpath=/html/body/div[2]/div[2]/div/div/div/div[1]/div[2]/div[1]/div[3]/span'
    try:
        waiting_room_element = page.locator(waiting_room_xpath)
        if await waiting_room_element.count() > 0 and await waiting_room_element.is_visible():
            dash(f"{tag} in waiting room...")
            while True:
                try:
                    if await waiting_room_element.count() == 0 or not await waiting_room_element.is_visible():
                        break
                    for indicator in [
                        'xpath=//button[contains(@aria-label, "mute")]',
                        'xpath=//button[contains(text(), "Participants")]',
                        'xpath=//button[contains(@aria-label, "Leave")]'
                    ]:
                        if await page.locator(indicator).count() > 0: return True
                    await asyncio.sleep(2)
                except: await asyncio.sleep(2)
        else:
            dash(f"{tag} no waiting room")
    except: pass
    return True

# ========== POOL ==========
_pool       = {}
_pool_lock  = threading.Lock()
_pool_loop  = None
_cmd_events = {}
_cmd_data   = {}

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
            ]
        )
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

        try:
            # Navigate
            await page.goto(get_zoom_url(meeting_code), timeout=120000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if stop(): return

            # ── NAME INPUT ──
            name_input = page.locator('#input-for-name')
            if await name_input.count() == 0:
                for sel in ['xpath=//*[@id="input-for-name"]',
                            'input[placeholder*="name" i]',
                            'input[aria-label*="name" i]']:
                    name_input = page.locator(sel)
                    if await name_input.count() > 0: break
            await name_input.first.wait_for(state="visible", timeout=60000)
            await asyncio.sleep(0.5)
            name = get_name(name_mode, custom_names, slot_idx)
            await name_input.first.fill(name)
            dash(f"{tag} name filled: {name}")
            if stop(): return

            # ── PASSCODE (upfront on join page) ──
            if passcode:
                for selector in [
                    '#input-for-password',
                    'xpath=//*[@id="input-for-password"]',
                    'xpath=//input[@type="password"]',
                    'xpath=//input[contains(@placeholder, "code")]',
                    'xpath=//input[contains(@aria-label, "code")]',
                    'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input'
                ]:
                    try:
                        pass_input = page.locator(selector)
                        if await pass_input.count() > 0:
                            await pass_input.first.wait_for(state="visible", timeout=10000)
                            await asyncio.sleep(0.5)
                            await pass_input.first.fill(passcode)
                            dash(f"{tag} passcode filled ✅")
                            break
                    except: continue

            # ── SYNC BARRIER — all bots wait here ──
            async with _sync_barrier():
                pass
            if stop(): return

            # ── STEP 1: First Join (name screen → preview screen) ──
            for attempt in range(3):
                try:
                    result = await page.evaluate("""() => {
                        const all = [...document.querySelectorAll('button')];
                        let b = all.find(b =>
                            b.textContent.trim().match(/^Join/) &&
                            !b.className.includes('disabled') && !b.disabled
                        );
                        if (!b) b = all.find(b => b.textContent.includes('Join'));
                        if (b) { b.click(); return b.textContent.trim(); }
                        return null;
                    }""")
                    if result:
                        dash(f"{tag} step1 join: '{result}'")
                        break
                except: pass
                await asyncio.sleep(3)
            if stop(): return

            # ── STEP 2: Preview screen Join (wait for enabled) ──
            await asyncio.sleep(2)
            for attempt in range(20):
                if stop(): break
                try:
                    result = await page.evaluate("""() => {
                        const all = [...document.querySelectorAll('button')];
                        let b = all.find(b =>
                            b.className.includes('preview-join-button') &&
                            !b.className.includes('disabled') && !b.disabled
                        );
                        if (!b) b = all.find(b =>
                            b.textContent.trim().match(/^Join/) &&
                            !b.className.includes('disabled') && !b.disabled
                        );
                        if (b) { b.click(); return b.textContent.trim(); }
                        return null;
                    }""")
                    if result:
                        dash(f"{tag} joining now! ✅")
                        break
                except: pass
                await asyncio.sleep(1)
            if stop(): return

            # ── PASSCODE POPUP (after join click) ──
            if passcode:
                await page.wait_for_timeout(2000)
                for selector in ['#input-for-password',
                                  'xpath=//input[@type="password"]',
                                  'xpath=//*[@id="input-for-password"]']:
                    try:
                        pi = page.locator(selector)
                        if await pi.count() > 0:
                            await pi.first.wait_for(state="visible", timeout=4000)
                            await pi.first.fill(passcode)
                            dash(f"{tag} passcode popup filled ✅")
                            for cs in ['xpath=//button[contains(text(),"Join Meeting")]',
                                       'xpath=//button[contains(text(),"Join")]',
                                       'xpath=//button[@type="submit"]']:
                                try:
                                    cb = page.locator(cs)
                                    if await cb.count() > 0:
                                        await cb.first.click(); break
                                except: continue
                            break
                    except: continue

            await wait_for_meeting_to_start(page, tag)
            if stop(): return
            await wait_for_waiting_room(page, tag)
            if stop(): return
            await join_audio_computer(page, tag)

            sync_print(f"{tag} IN MEETING")

            end_time = asyncio.get_event_loop().time() + duration * 60
            while asyncio.get_event_loop().time() < end_time:
                if stop(): break
                await asyncio.sleep(2)

        except Exception as e:
            if "TERMINATED" not in str(e):
                dash(f"{tag} error: {e}")
        finally:
            for obj in [page, context, browser]:
                if obj:
                    try: await obj.close()
                    except: pass

    except Exception as e:
        if "TERMINATED" not in str(e):
            dash(f"{tag} launch error: {e}")
    finally:
        with _pool_lock: _pool.pop(slot_idx, None)
        running_bots.pop(bot_id, None)
        terminate_flags.pop(bot_id, None)


def _start_pool():
    global _pool_loop
    def _run():
        global _pool_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _pool_loop = loop
        tasks = [loop.create_task(_pool_slot(i)) for i in range(MAX_USERS_PER_INSTANCE)]
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        try: loop.close()
        except: pass
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
    custom_names = data.get('customNames', None)  # already sliced per-instance by dashboard

    with _pool_lock:
        idle_slots = [i for i, s in _pool.items() if s.get('state') == 'idle']

    use_slots = idle_slots[:users]
    if not use_slots:
        sync_print("No idle windows — pool still opening, please wait"); return

    with _pool_lock:
        for idx in use_slots:
            if idx in _pool: _pool[idx]['state'] = 'loading'

    for idx in use_slots:
        _cmd_data[idx] = {
            'meetingCode': meeting_code, 'passcode': passcode,
            'nameMode': name_mode, 'customNames': custom_names, 'duration': duration
        }

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

# ========== TERMINATE ==========
@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots, _pool, _cmd_events, _cmd_data

    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    if meeting_id: meeting_id = str(meeting_id).replace(' ','')
    is_all = meeting_id in ('all', None, '')

    sync_print(f"Terminating {'all' if is_all else meeting_id}...")

    with _pool_lock:
        for s in _pool.values():
            bid = s.get('bot_id')
            if bid: terminate_flags[bid] = True
    for bid in list(running_bots.keys()):
        terminate_flags[bid] = True

    if _pool_loop and not _pool_loop.is_closed():
        for evt in list(_cmd_events.values()):
            try: _pool_loop.call_soon_threadsafe(evt.set)
            except: pass

    def cleanup():
        global current_bots, _pool, _cmd_events, _cmd_data
        try: os.system("pkill -9 -f chromium 2>/dev/null")
        except: pass
        time.sleep(0.5)
        try: os.system("rm -rf /tmp/.org.chromium.* /tmp/playwright* 2>/dev/null")
        except: pass
        running_bots.clear()
        terminate_flags.clear()
        with _pool_lock: _pool.clear()
        _cmd_events.clear()
        _cmd_data.clear()
        with bot_lock: current_bots = 0
        gc.collect()
        sync_print("Killed | restarting pool...")
        try:
            sio.emit('terminateAck', {'instanceId': INSTANCE_ID, 'killed': 0})
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': 0, 'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass
        time.sleep(0.5)
        _start_pool()

    threading.Thread(target=cleanup, daemon=True).start()

# ========== SHUTDOWN ==========
@sio.on('shutdown')
def handle_shutdown(_=None):
    sync_print("Shutdown received")
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
    sync_print(f"Connected | opening {MAX_USERS_PER_INSTANCE} windows...")
    sio.emit('register', {'instanceId': INSTANCE_ID,
                          'currentUsers': current_bots, 'maxUsers': MAX_USERS_PER_INSTANCE})
    _start_pool()

@sio.event
def disconnect():
    sync_print("Disconnected")

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
                if idle != prev_idle:
                    if idle == MAX_USERS_PER_INSTANCE:
                        sync_print(f"All {idle} windows ready ✅")
                    prev_idle = idle
                sio.emit('poolStatus', {'instanceId': INSTANCE_ID, 'idle': idle})
        except: pass
        time.sleep(5)

try:
    sio.connect(NGROK_URL, transports=['websocket','polling'])
    threading.Thread(target=heartbeat_loop, daemon=True).start()
except Exception as e:
    sync_print(f"Connect failed: {e}")

while True:
    time.sleep(1)
