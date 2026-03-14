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
_bot_loop = None
PAGE_LOAD_SEM = None

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

# ========== SYNC BARRIER ==========
READY_TO_JOIN = asyncio.Event()
BOTS_READY = 0
BOTS_TOTAL = 0
BOTS_FAILED = 0
BOTS_LOCK = asyncio.Lock()

async def wait_for_all_bots():
    global BOTS_READY, BOTS_TOTAL
    async with BOTS_LOCK:
        BOTS_READY += 1
        ready = BOTS_READY
        total = BOTS_TOTAL
        failed = BOTS_FAILED
    sync_print(f"[SYNC] {ready}/{total} ready (failed:{failed})")
    if ready + failed >= total:
        READY_TO_JOIN.set()
        sync_print("[SYNC] All ready! Joining together...")
    await READY_TO_JOIN.wait()

async def _unblock_sync():
    READY_TO_JOIN.set()

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

# ========== PRE-LOAD POOL ==========
# Each slot: browser pre-opened, waits for preload command (meeting URL + naam fill)
# Then waits for launch command (just clicks join)

_pool = {}        # slot_idx -> {browser, context, page, name, state}
                  # state: 'idle' | 'loading' | 'ready' | 'joining' | 'in_meeting'
_pool_lock = threading.Lock()
_pool_loop = None
_preload_events = {}   # slot_idx -> asyncio.Event (signals preload data arrived)
_launch_events  = {}   # slot_idx -> asyncio.Event (signals launch)
_preload_data   = {}   # slot_idx -> {meetingCode, passcode, nameMode, customNames, duration}
_POOL_SIZE = MAX_USERS_PER_INSTANCE

async def _pool_slot(slot_idx):
    """One browser window — lives until terminate/shutdown."""
    tag = f"[{INSTANCE_ID}-{slot_idx+1:02d}]"
    bot_id = f"pw-{INSTANCE_ID}-{slot_idx+1:02d}"
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
            _pool[slot_idx] = {'browser': browser, 'context': context,
                               'page': page, 'state': 'idle', 'bot_id': bot_id}

        sync_print(f"{tag} window open — waiting for pre-load")

        # Wait for preload signal (meeting URL + naam fill)
        preload_evt = asyncio.Event()
        _preload_events[slot_idx] = preload_evt
        await preload_evt.wait()

        # Check if terminated during wait
        if terminate_flags.get(bot_id):
            return

        data = _preload_data.get(slot_idx, {})
        meeting_code = data.get('meetingCode', '')
        passcode     = str(data.get('passcode', '') or '').strip()
        name_mode    = data.get('nameMode', 'indian')
        custom_names = data.get('customNames', None)
        duration     = data.get('duration', 90)

        with _pool_lock:
            if slot_idx in _pool:
                _pool[slot_idx]['state'] = 'loading'

        # Navigate to meeting URL
        sync_print(f"{tag} pre-loading: {meeting_code}")
        await page.goto(get_zoom_url(meeting_code), timeout=120000)
        await page.wait_for_timeout(3000)

        if terminate_flags.get(bot_id): return

        # Fill name
        name = get_name(name_mode, custom_names, slot_idx)
        try:
            ni = page.locator('xpath=//*[@id="input-for-name"]')
            await ni.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(0.5)
            await ni.fill(name)
            sync_print(f"{tag} pre-loaded: name={name}")
        except Exception as e:
            sync_print(f"{tag} name fill failed: {e}")
            with _pool_lock:
                if slot_idx in _pool: _pool[slot_idx]['state'] = 'error'
            return

        # Fill passcode if needed
        if passcode:
            try:
                for sel in ['xpath=//input[@type="password"]',
                            'xpath=//*[@id="input-for-password"]',
                            'xpath=//input[contains(@placeholder,"code")]']:
                    pi = page.locator(sel)
                    if await pi.count() > 0:
                        await pi.first.wait_for(state="visible", timeout=3000)
                        await asyncio.sleep(0.5)
                        await pi.first.fill(passcode)
                        sync_print(f"{tag} passcode pre-filled")
                        break
            except: pass

        with _pool_lock:
            if slot_idx in _pool:
                _pool[slot_idx]['state'] = 'ready'
                _pool[slot_idx]['name'] = name
                _pool[slot_idx]['meeting_id'] = str(meeting_code).replace(' ','')

        running_bots[bot_id] = {'browser': browser,
                                 'meeting_id': str(meeting_code).replace(' ','')}
        terminate_flags[bot_id] = False

        sync_print(f"{tag} READY — waiting for launch signal")

        # Wait for launch signal
        launch_evt = asyncio.Event()
        _launch_events[slot_idx] = launch_evt
        await launch_evt.wait()

        if terminate_flags.get(bot_id): return

        with _pool_lock:
            if slot_idx in _pool: _pool[slot_idx]['state'] = 'joining'

        def stop():
            return terminate_flags.get(bot_id, False)

        # Click join
        try:
            join_btn = None
            for sel in ['xpath=//button[contains(text(),"Join")]',
                        'xpath=//button[contains(@class,"join")]',
                        'xpath=//*[@id="root"]/div/div[1]/div/div[2]/button']:
                try:
                    jb = page.locator(sel)
                    if await jb.count() > 0:
                        await jb.first.wait_for(state="visible", timeout=5000)
                        join_btn = jb.first
                        break
                except: continue
            if join_btn:
                await asyncio.sleep(random.uniform(0.2, 0.8))
                await join_btn.click()
                sync_print(f"{tag} join clicked — entering meeting")
            else:
                sync_print(f"{tag} join btn not found")
        except Exception as e:
            sync_print(f"{tag} join error: {e}")

        await wait_for_meeting_to_start(page, tag)
        await wait_for_waiting_room(page, tag)
        await join_audio_computer(page, tag)

        with _pool_lock:
            if slot_idx in _pool: _pool[slot_idx]['state'] = 'in_meeting'

        sync_print(f"{tag} IN MEETING — staying {duration} min")

        end_time = asyncio.get_event_loop().time() + duration * 60
        while asyncio.get_event_loop().time() < end_time:
            if stop():
                sync_print(f"{tag} terminated")
                break
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
        tasks = [loop.create_task(_pool_slot(i)) for i in range(_POOL_SIZE)]
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        try: loop.close()
        except: pass
        sync_print("Pool ended")
    threading.Thread(target=_run, daemon=True).start()
    sync_print(f"Opening {_POOL_SIZE} browser windows...")

# ========== PRELOAD HANDLER ==========
@sio.on(f'preload_{INSTANCE_ID}')
def handle_preload(data):
    """Dashboard sends meeting details — bots navigate + fill name immediately."""
    meeting_code = data.get('meetingCode', '')
    passcode     = str(data.get('passcode', '') or '').strip()
    users        = data.get('users', MAX_USERS_PER_INSTANCE)
    name_mode    = data.get('nameMode', 'indian')
    custom_names = data.get('customNames', None)
    duration     = data.get('duration', 90)

    if not meeting_code:
        sync_print("preload: no meetingCode"); return

    sync_print(f"Pre-loading {users} bots for meeting {meeting_code}")

    with _pool_lock:
        idle_slots = [i for i, s in _pool.items() if s.get('state') == 'idle']

    use_slots = idle_slots[:users]
    if not use_slots:
        sync_print("No idle windows! Pool may still be opening...")
        return

    for idx in use_slots:
        _preload_data[idx] = {
            'meetingCode': meeting_code, 'passcode': passcode,
            'nameMode': name_mode, 'customNames': custom_names, 'duration': duration
        }

    if _pool_loop and not _pool_loop.is_closed():
        for idx in use_slots:
            evt = _preload_events.get(idx)
            if evt:
                _pool_loop.call_soon_threadsafe(evt.set)

    sync_print(f"Sent pre-load signal to {len(use_slots)} windows")
    try:
        sio.emit('preloadAck', {'instanceId': INSTANCE_ID, 'slots': len(use_slots)})
    except: pass

# ========== LAUNCH HANDLER (instant join) ==========
@sio.on(f'command_{INSTANCE_ID}')
def handle_command(data):
    global current_bots, BOTS_TOTAL, BOTS_READY, BOTS_FAILED, READY_TO_JOIN, _bot_loop, PAGE_LOAD_SEM

    if data.get('action') in ['terminate', 'terminate_all']:
        handle_terminate(data); return

    users        = data.get('users', 1)
    meeting_code = data.get('meetingCode', '')
    passcode     = str(data.get('passcode', '') or '').strip()
    duration     = data.get('duration', 90)
    headless     = data.get('headless', True)
    name_mode    = data.get('nameMode', 'indian')
    custom_names = data.get('customNames', None)

    if not meeting_code:
        sync_print("ERROR: meetingCode missing"); return

    mid = str(meeting_code).replace(' ','')

    # Check if pre-loaded bots are ready for this meeting
    with _pool_lock:
        ready_slots = [i for i, s in _pool.items()
                       if s.get('state') == 'ready' and
                       str(s.get('meeting_id','')).replace(' ','') == mid]

    if ready_slots:
        # INSTANT JOIN PATH — bots already on meeting page with name filled
        use_slots = ready_slots[:users]
        sync_print(f"INSTANT JOIN: firing {len(use_slots)} pre-loaded bots!")

        with _pool_lock:
            for idx in use_slots:
                if idx in _pool: _pool[idx]['state'] = 'joining'

        if _pool_loop and not _pool_loop.is_closed():
            for idx in use_slots:
                evt = _launch_events.get(idx)
                if evt:
                    _pool_loop.call_soon_threadsafe(evt.set)

        remaining = users - len(use_slots)
        with bot_lock:
            current_bots += len(use_slots)
        try:
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': current_bots,
                                        'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass

        if remaining <= 0:
            return
        # Fall through to launch remaining normally
        users = remaining
        sync_print(f"{remaining} extra bots launching normally...")

    # NORMAL LAUNCH PATH (fallback if not pre-loaded)
    sync_print(f"Starting {users} bots normally | meeting={meeting_code}")

    def run_automation():
        global BOTS_TOTAL, BOTS_READY, BOTS_FAILED, READY_TO_JOIN, _bot_loop, current_bots, PAGE_LOAD_SEM

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _bot_loop = loop

        BOTS_TOTAL    = users
        BOTS_READY    = 0
        BOTS_FAILED   = 0
        READY_TO_JOIN = asyncio.Event()
        PAGE_LOAD_SEM = asyncio.Semaphore(2)

        tasks = [
            loop.create_task(
                start(f"[{INSTANCE_ID}-{i+1:02d}]", duration*60, meeting_code,
                      passcode, headless, bot_index=i,
                      bot_id=f"{INSTANCE_ID}-{i+1:02d}-{meeting_code}",
                      name_mode=name_mode, custom_names=custom_names)
            )
            for i in range(users)
        ]
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        try: loop.run_until_complete(loop.shutdown_asyncgens())
        except: pass
        try: loop.close()
        except: pass
        gc.collect()

        with bot_lock:
            current_bots = max(0, current_bots - users)
        sync_print(f"Batch done: {users} bots")
        try:
            sio.emit('instanceUpdate', {'instanceId': INSTANCE_ID,
                                        'currentUsers': current_bots,
                                        'maxUsers': MAX_USERS_PER_INSTANCE})
        except: pass

    with bot_lock:
        if current_bots + users <= MAX_USERS_PER_INSTANCE:
            current_bots += users
            threading.Thread(target=run_automation, daemon=True).start()
        else:
            sync_print(f"Capacity full ({current_bots}/{MAX_USERS_PER_INSTANCE})")

# ========== NORMAL START (fallback) ==========
async def start(tag, wait_time, meetingcode, passcode, headless,
                bot_index=0, bot_id=None, name_mode="indian", custom_names=None):
    global BOTS_FAILED

    if bot_id:
        terminate_flags[bot_id] = False

    def stop():
        return bot_id and terminate_flags.get(bot_id, False)

    await PAGE_LOAD_SEM.acquire()
    browser = None; context = None; page = None; sem_released = False

    try:
        if stop():
            PAGE_LOAD_SEM.release(); return

        p_inst = async_playwright()
        p = await p_inst.__aenter__()
        browser = await p.chromium.launch(
            headless=headless,
            args=['--no-sandbox','--disable-dev-shm-usage',
                  '--use-fake-device-for-media-stream',
                  '--use-file-for-fake-audio-capture=/dev/null',
                  '--mute-audio','--disable-camera','--disable-video-capture',
                  '--disable-gpu','--window-size=1280,720']
        )
        if bot_id:
            running_bots[bot_id] = {'browser': browser, 'meeting_id': str(meetingcode).replace(' ','')}

        context = await browser.new_context(permissions=[], viewport={"width":1280,"height":720})
        page    = await context.new_page()
        await page.goto(get_zoom_url(meetingcode), timeout=120000)
        await page.wait_for_timeout(4000)

        try:
            ni = page.locator('xpath=//*[@id="input-for-name"]')
            await ni.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(1)
            user_name = get_name(name_mode, custom_names, bot_index)
            await ni.fill(user_name)
            sync_print(f"{tag} name filled: {user_name}")
        except Exception as e:
            sync_print(f"{tag} name fill failed: {e}")
            async with BOTS_LOCK:
                BOTS_FAILED += 1
            PAGE_LOAD_SEM.release(); sem_released = True
            try: await browser.close()
            except: pass
            running_bots.pop(bot_id, None); terminate_flags.pop(bot_id, None)
            return

        PAGE_LOAD_SEM.release(); sem_released = True
        if stop(): raise Exception("TERMINATED")

        passcode = str(passcode or '').strip()
        if passcode:
            try:
                pass_input = None
                for sel in ['xpath=//input[@type="password"]',
                            'xpath=//*[@id="input-for-password"]',
                            'xpath=//input[contains(@placeholder,"code")]',
                            'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input']:
                    try:
                        pi = page.locator(sel)
                        if await pi.count() > 0:
                            await pi.first.wait_for(state="visible", timeout=5000)
                            pass_input = pi.first; break
                    except: continue
                if pass_input:
                    await asyncio.sleep(1.5)
                    await pass_input.fill(passcode)
                    sync_print(f"{tag} passcode filled")
            except Exception as e:
                sync_print(f"{tag} passcode error: {e}")

        await wait_for_all_bots()
        if stop(): raise Exception("TERMINATED")

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
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await join_btn.click()
                sync_print(f"{tag} join clicked")
            else:
                sync_print(f"{tag} join button not found"); return
        except Exception as e:
            sync_print(f"{tag} join error: {e}"); return

        await wait_for_meeting_to_start(page, tag)
        await wait_for_waiting_room(page, tag)
        await join_audio_computer(page, tag)

        sync_print(f"{tag} in meeting, staying {wait_time//60} min")
        elapsed = 0
        while elapsed < wait_time:
            if stop():
                sync_print(f"{tag} terminated"); break
            await asyncio.sleep(2); elapsed += 2

    except Exception as e:
        if "TERMINATED" not in str(e):
            sync_print(f"{tag} error: {e}")
        async with BOTS_LOCK:
            BOTS_FAILED += 1
        if not sem_released:
            try: PAGE_LOAD_SEM.release()
            except: pass
    finally:
        for obj in [page, context, browser]:
            if obj:
                try: await obj.close()
                except: pass
        gc.collect()
        running_bots.pop(bot_id, None); terminate_flags.pop(bot_id, None)
        sync_print(f"{tag} done")

# ========== TERMINATE ==========
@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots

    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    action = data.get('action','') if isinstance(data, dict) else ''

    if meeting_id in ('all', None, '') or action == 'terminate_all':
        targets = list(running_bots.items())
        # Also kill all pool slots
        with _pool_lock:
            pool_bids = [s.get('bot_id') for s in _pool.values() if s.get('bot_id')]
        for bid in pool_bids:
            terminate_flags[bid] = True
    else:
        mid = str(meeting_id).replace(' ','')
        targets = [(bid, b) for bid, b in list(running_bots.items())
                   if str(b.get('meeting_id','')).replace(' ','') == mid]

    killed = len(targets)
    sync_print(f"Terminating {killed} bots...")

    for bid, _ in targets:
        terminate_flags[bid] = True

    all_mtg = [bid for bid, b in list(running_bots.items())
               if meeting_id in ('all', None, '') or
               str(b.get('meeting_id','')).replace(' ','') == str(meeting_id).replace(' ','')]
    if len(all_mtg) <= killed:
        try:
            loop = _pool_loop or _bot_loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_unblock_sync(), loop)
        except: pass
        # Also unblock any waiting pool slots
        if _pool_loop and not _pool_loop.is_closed():
            for evt in list(_preload_events.values()):
                try: _pool_loop.call_soon_threadsafe(evt.set)
                except: pass
            for evt in list(_launch_events.values()):
                try: _pool_loop.call_soon_threadsafe(evt.set)
                except: pass

    def cleanup():
        global current_bots
        futures = []
        for bid, info in targets:
            try:
                br = info.get('browser')
                loop = _pool_loop or _bot_loop
                if br and loop and loop.is_running():
                    futures.append(asyncio.run_coroutine_threadsafe(br.close(), loop))
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

@sio.on('shutdown')
def handle_shutdown(_=None):
    sync_print("Shutdown received")
    try:
        with open('/content/SHUTDOWN_NOW','w') as f: f.write('1')
    except: pass
    handle_terminate({'meetingId':'all'})
    try:
        from google.colab import runtime
        runtime.unassign()
    except: pass

@sio.event
def connect():
    sync_print("Connected to server")
    sio.emit('register', {'instanceId': INSTANCE_ID,
                          'currentUsers': current_bots, 'maxUsers': MAX_USERS_PER_INSTANCE})
    # Auto-start pool on connect
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
                # Also report pool status
                with _pool_lock:
                    idle  = sum(1 for s in _pool.values() if s.get('state')=='idle')
                    ready = sum(1 for s in _pool.values() if s.get('state')=='ready')
                if idle > 0 or ready > 0:
                    sio.emit('poolStatus', {'instanceId': INSTANCE_ID,
                                            'idle': idle, 'ready': ready})
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
