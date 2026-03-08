import base64;exec(base64.b64decode("aW1wb3J0IG9zCm9zLnN5c3RlbSgicGlwIGluc3RhbGwgcGxheXdyaWdodCBmYWtlciBuZXN0LWFzeW5jaW8gcHl0aG9uLXNvY2tldGlvIHJlcXVlc3RzIGluZGlhbi1uYW1lcyA+L2Rldi9udWxsIDI+JjEiKQpvcy5zeXN0ZW0oInBsYXl3cmlnaHQgaW5zdGFsbCBjaHJvbWl1bSA+L2Rldi9udWxsIDI+JjEiKQpvcy5zeXN0ZW0oInBsYXl3cmlnaHQgaW5zdGFsbC1kZXBzID4vZGV2L251bGwgMj4mMSIpCnByaW50KCLinIUgQWxsIERlcGVuZGVuY2llcyBJbnN0YWxsZWQiKQo=").decode())

import threading
import asyncio
import base64
import random
import gc
import os
from datetime import datetime
import indian_names
from faker import Faker
from playwright.async_api import async_playwright
import nest_asyncio
import socketio
import time

nest_asyncio.apply()
fake_en = Faker('en_US')

# ========== CONFIG ==========
import argparse as _ap
_parser = _ap.ArgumentParser()
_parser.add_argument('--server', type=str, default="https://extensional-christene-intensionally.ngrok-free.dev")
_args, _ = _parser.parse_known_args()
NGROK_URL = _args.server
INSTANCE_ID = f"colab-{int(time.time()*1000)%100000}"
MAX_USERS_PER_INSTANCE = 10
current_bots = 0
bot_lock = threading.Lock()
running_bots = {}
terminate_flags = {}
_bot_loop = None
PAGE_LOAD_SEM = None

print(f"[{datetime.now().strftime('%H:%M:%S')}] ID={INSTANCE_ID} | Max={MAX_USERS_PER_INSTANCE}")

sio = socketio.Client(reconnection=True)
MUTEX = threading.Lock()

def sync_print(msg):
    with MUTEX:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    try:
        sio.emit('botLog', {'instanceId': INSTANCE_ID, 'msg': msg,
                            'ts': datetime.now().strftime('%H:%M:%S')})
    except: pass

# ========== NAME ==========
def get_name(mode="indian", custom_list=None, index=0):
    if mode == "custom" and custom_list:
        return custom_list[index % len(custom_list)]
    elif mode == "english":
        return fake_en.name()
    return indian_names.get_full_name(gender=random.choice(['male', 'female']))

# ========== ZOOM URL ==========
ZOOM_PARTS = {
    'domain': base64.b64decode('em9vbS51cw==').decode(),
    'join_path': base64.b64decode('d2Mvam9pbg==').decode()
}
def get_zoom_url(meeting_code):
    return f"https://{ZOOM_PARTS['domain']}/{ZOOM_PARTS['join_path']}/{meeting_code}"

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
    sync_print(f"[SYNC] {ready}/{total} bots ready (failed: {failed})")
    if ready + failed >= total:
        READY_TO_JOIN.set()
        sync_print("[SYNC] All bots ready! Joining together...")
    await READY_TO_JOIN.wait()

async def _unblock_sync():
    READY_TO_JOIN.set()

# ========== AUDIO / WAIT HELPERS ==========
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
                    sync_print(f"{tag} audio joined")
                    return True
            except:
                continue
        muted_btn = page.locator('xpath=//button[contains(@aria-label, "mute") or contains(@aria-label, "Mute")]')
        if await muted_btn.count() > 0:
            sync_print(f"{tag} already has audio")
            return True
    except Exception as e:
        sync_print(f"{tag} audio join skipped: {e}")
    return False

async def wait_for_meeting_to_start(page, tag):
    waiting_xpath = 'xpath=//*[@id="root"]/div/div[2]/div[1]/div[3]/span'
    sync_print(f"{tag} checking if meeting is live...")
    try:
        waiting_element = page.locator(waiting_xpath)
        if await waiting_element.count() > 0 and await waiting_element.is_visible():
            sync_print(f"{tag} meeting is NOT live! Waiting for host to start...")
            while True:
                try:
                    if await waiting_element.count() == 0 or not await waiting_element.is_visible():
                        sync_print(f"{tag} meeting has started! Proceeding...")
                        break
                    for indicator in [
                        'xpath=//button[contains(@aria-label, "mute")]',
                        'xpath=//button[contains(text(), "Participants")]',
                        'xpath=//button[contains(@aria-label, "Leave")]'
                    ]:
                        if await page.locator(indicator).count() > 0:
                            sync_print(f"{tag} meeting started (detected by indicator)!")
                            return True
                    await asyncio.sleep(2)
                except:
                    await asyncio.sleep(2)
                    continue
        else:
            sync_print(f"{tag} meeting is live! No waiting required.")
    except Exception as e:
        sync_print(f"{tag} error while checking meeting status: {e}")
    return True

async def wait_for_waiting_room(page, tag):
    waiting_room_xpath = 'xpath=/html/body/div[2]/div[2]/div/div/div/div[1]/div[2]/div[1]/div[3]/span'
    sync_print(f"{tag} checking for waiting room...")
    try:
        waiting_room_element = page.locator(waiting_room_xpath)
        if await waiting_room_element.count() > 0 and await waiting_room_element.is_visible():
            sync_print(f"{tag} IN WAITING ROOM! Waiting for host to admit...")
            while True:
                try:
                    if await waiting_room_element.count() == 0 or not await waiting_room_element.is_visible():
                        sync_print(f"{tag} admitted to meeting! Proceeding...")
                        break
                    for indicator in [
                        'xpath=//button[contains(@aria-label, "mute")]',
                        'xpath=//button[contains(text(), "Participants")]',
                        'xpath=//button[contains(@aria-label, "Leave")]'
                    ]:
                        if await page.locator(indicator).count() > 0:
                            sync_print(f"{tag} admitted to meeting (detected by indicator)!")
                            return True
                    await asyncio.sleep(2)
                except:
                    await asyncio.sleep(2)
                    continue
        else:
            sync_print(f"{tag} no waiting room detected")
    except Exception as e:
        sync_print(f"{tag} error while checking waiting room: {e}")
    return True

# ========== MAIN BOT ==========
async def start(tag, wait_time, meetingcode, passcode, headless,
                bot_index=0, bot_id=None, name_mode="indian", custom_names=None):
    global BOTS_FAILED

    if bot_id:
        terminate_flags[bot_id] = False

    def stop():
        return bot_id and terminate_flags.get(bot_id, False)

    await PAGE_LOAD_SEM.acquire()

    browser = None
    context = None
    page    = None
    sem_released = False

    try:
        if stop():
            PAGE_LOAD_SEM.release()
            return

        p_inst = async_playwright()
        p = await p_inst.__aenter__()

        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox', '--disable-dev-shm-usage',
                '--use-fake-device-for-media-stream',
                '--use-file-for-fake-audio-capture=/dev/null',
                '--mute-audio', '--disable-camera', '--disable-video-capture',
                '--disable-gpu', '--window-size=1280,720',
            ]
        )

        if bot_id:
            running_bots[bot_id] = {'browser': browser, 'meeting_id': str(meetingcode).replace(' ','')}

        context = await browser.new_context(permissions=[], viewport={"width": 1280, "height": 720})
        page    = await context.new_page()

        zoom_url = get_zoom_url(meetingcode)
        await page.goto(zoom_url, timeout=120000)
        await page.wait_for_timeout(4000)

        # NAME INPUT
        try:
            name_input = page.locator('xpath=//*[@id="input-for-name"]')
            await name_input.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(1)
            user_name = get_name(name_mode, custom_names, bot_index)
            await name_input.fill(user_name)
            sync_print(f"{tag} name filled: {user_name}")
        except Exception as e:
            sync_print(f"{tag} name fill failed: {e}")
            async with BOTS_LOCK:
                BOTS_FAILED += 1
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
        if passcode is not None and passcode != "":
            sync_print(f"{tag} attempting to enter passcode: {passcode}")
            try:
                passcode_selectors = [
                    'xpath=//input[@type="password"]',
                    'xpath=//input[contains(@placeholder, "code")]',
                    'xpath=//input[contains(@aria-label, "code")]',
                    'xpath=//*[@id="input-for-password"]',
                    'xpath=/html/body/div[2]/div[2]/div/div[1]/div/div[2]/div[2]/div/input'
                ]
                pass_input = None
                for selector in passcode_selectors:
                    try:
                        pass_input = page.locator(selector)
                        if await pass_input.count() > 0:
                            await pass_input.first.wait_for(state="visible", timeout=5000)
                            pass_input = pass_input.first
                            break
                    except:
                        continue
                if pass_input:
                    await asyncio.sleep(1.5)
                    await pass_input.fill(passcode)
                    sync_print(f"{tag} passcode filled: {passcode}")
                else:
                    sync_print(f"{tag} no passcode field found")
            except Exception as e:
                sync_print(f"{tag} passcode fill error: {e}")
        else:
            sync_print(f"{tag} no passcode provided (empty), skipping passcode field")

        # SYNC BARRIER
        await wait_for_all_bots()
        if stop(): raise Exception("TERMINATED")

        # JOIN BUTTON
        try:
            join_selectors = [
                'xpath=//button[contains(text(), "Join")]',
                'xpath=//button[contains(@class, "join")]',
                'xpath=//*[@id="root"]/div/div[1]/div/div[2]/button'
            ]
            join_btn = None
            for selector in join_selectors:
                try:
                    join_btn = page.locator(selector)
                    if await join_btn.count() > 0:
                        await join_btn.first.wait_for(state="visible", timeout=5000)
                        join_btn = join_btn.first
                        break
                except:
                    continue

            if join_btn:
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await join_btn.click()
                sync_print(f"{tag} join clicked")
            else:
                sync_print(f"{tag} join button not found")
                try: await browser.close()
                except: pass
                return
        except Exception as e:
            sync_print(f"{tag} join click failed: {e}")
            try: await browser.close()
            except: pass
            return

        await wait_for_meeting_to_start(page, tag)
        await wait_for_waiting_room(page, tag)
        await join_audio_computer(page, tag)

        sync_print(f"{tag} now staying for {wait_time//60} minutes")
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
        running_bots.pop(bot_id, None)
        terminate_flags.pop(bot_id, None)
        sync_print(f"{tag} done")

# ========== TERMINATE ==========
@sio.on(f'terminate_{INSTANCE_ID}')
def handle_terminate(data):
    global current_bots

    meeting_id = data.get('meetingId') if isinstance(data, dict) else data
    if meeting_id: meeting_id = str(meeting_id).replace(' ', '')
    if meeting_id in ('all', None, ''):
        targets = list(running_bots.items())
    else:
        targets = [(bid, b) for bid, b in list(running_bots.items())
                   if str(b.get('meeting_id','')).replace(' ','') == meeting_id]

    killed = len(targets)
    sync_print(f"Terminating {killed} bots...")

    for bot_id, _ in targets:
        terminate_flags[bot_id] = True

    all_meeting_bots = [bid for bid, b in list(running_bots.items())
                        if meeting_id == 'all' or str(b.get('meeting_id','')).replace(' ','') == meeting_id]
    if len(all_meeting_bots) == killed:
        try:
            if _bot_loop and _bot_loop.is_running():
                asyncio.run_coroutine_threadsafe(_unblock_sync(), _bot_loop)
        except: pass

    def cleanup():
        global current_bots

        futures = []
        for bot_id, info in targets:
            try:
                br = info.get('browser')
                if br and _bot_loop and _bot_loop.is_running():
                    futures.append(asyncio.run_coroutine_threadsafe(br.close(), _bot_loop))
            except: pass
        for f in futures:
            try: f.result(timeout=5)
            except: pass

        time.sleep(1)

        target_ids = [bid for bid, _ in targets]
        for bid in target_ids:
            running_bots.pop(bid, None)
            terminate_flags.pop(bid, None)
        with bot_lock:
            current_bots = max(0, current_bots - killed)

        if len(running_bots) == 0:
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

# ========== COMMAND ==========
@sio.on(f'command_{INSTANCE_ID}')
def handle_command(data):
    global current_bots, BOTS_TOTAL, BOTS_READY, BOTS_FAILED, READY_TO_JOIN, _bot_loop, PAGE_LOAD_SEM

    if data.get('action') in ['terminate', 'terminate_all']:
        handle_terminate(data); return

    users        = data.get('users', 1)
    meeting_code = data.get('meetingCode')
    if not meeting_code:
        sync_print("ERROR: meetingCode missing"); return

    passcode     = data.get('passcode', '')
    duration     = data.get('duration', 90)
    headless     = data.get('headless', True)
    name_mode    = data.get('nameMode', 'indian')
    custom_names = data.get('customNames', None)

    sync_print(f"Starting {users} bots | meeting={meeting_code} | mode={name_mode}")

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
                start(f"[{INSTANCE_ID}-{i+1:02d}]", duration * 60, meeting_code,
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

# ========== SOCKET EVENTS ==========
_SHOULD_UNASSIGN = False

@sio.on('doUnassign')
def handle_unassign(data=None):
    global _SHOULD_UNASSIGN
    sync_print("Server shutdown signal — unassigning Colab...")
    _SHOULD_UNASSIGN = True

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
    sync_print("Shutdown signal received — unassigning Colab runtime...")
    try:
        from google.colab import runtime
        runtime.unassign()
    except Exception as e:
        sync_print(f"Shutdown error: {e}")

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
    sio.connect(NGROK_URL, transports=['websocket', 'polling'])
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    sync_print("Ready")
except Exception as e:
    sync_print(f"Connect failed: {e}")

while True:
    time.sleep(1)
    if _SHOULD_UNASSIGN:
        # Write a trigger file that Cell 3 watches
        try:
            with open('/content/unassign_trigger.txt', 'w') as f:
                f.write('1')
            sync_print("Unassign trigger written — waiting for cell to pick up...")
        except Exception as e:
            sync_print(f"Trigger write error: {e}")
        break  # Exit main loop so cell finishes and next cell can run
