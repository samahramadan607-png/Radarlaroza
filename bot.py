import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re
import time
import binascii
import json
import threading
import os
from datetime import datetime
from html import escape
from Crypto.Cipher import AES

STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "bot_status.json")
STATE_FILE  = os.path.join(os.path.dirname(__file__), "..", "bot_series_state.json")
MANUAL_DOMAIN_FILE = os.path.join(os.path.dirname(__file__), "..", "manual_domain.json")
TARGET_SERIES_FILE = os.path.join(os.path.dirname(__file__), "..", "target_series.json") 

def save_status():
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_status, f, ensure_ascii=False)
    except: pass

def load_manual_domain():
    try:
        with open(MANUAL_DOMAIN_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("domain")
    except:
        return None

def save_manual_domain(domain):
    try:
        with open(MANUAL_DOMAIN_FILE, "w", encoding="utf-8") as f:
            json.dump({"domain": domain}, f, ensure_ascii=False)
    except: pass

def clear_manual_domain():
    try:
        if os.path.exists(MANUAL_DOMAIN_FILE):
            os.remove(MANUAL_DOMAIN_FILE)
    except: pass

def load_series_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_series_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except: pass

def load_target_series():
    try:
        with open(TARGET_SERIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except:
        default_series = {}
        save_target_series(default_series)
        return default_series

def save_target_series(data):
    try:
        with open(TARGET_SERIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[-] خطأ في حفظ المسلسلات: {e}")

TARGET_SERIES = load_target_series()

# ==========================================
# 1. الإعدادات
# ==========================================
YOUR_API_URL = "https://arabfleex.live/api.php" 
SECRET_KEY = "ArabFleex_2024_SecRet"
TELEGRAM_TOKEN = "8692766022:AAEsjS3IrZ3nafTa8WsRu70oKQ2lrsb5tkk"
TELEGRAM_CHAT_ID = "1013251619" 
NOTIFY_ONLY = True  

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

LAROZA_DOMAIN = "https://larooza.asia"
LAROZA_KNOWN_DOMAINS = [
    "https://larooza.asia",
    "https://laroza.lat",
    "https://larozza.forum",
    "https://larozza.beer",
    "https://larroza.baby",
    "https://larroza.click",
    "https://larroza.casa",
]

REAL_VIDEO_HOSTS = [
    "streamtape", "uqload", "doodstream", "dood.", "mp4upload",
    "voe.sx", "filemoon", "ok.ru", "vk.com", "youtube.com",
    "dailymotion", "upvid", "vidoza", "mixdrop", "upstream",
    "supervideo", "videobin", "fembed", "jawcloud", "jetload",
    "hxfile", "vtbe", "vidbom", "vidhide", "playtube",
    "waaw", "wolfstream", "embedgram", "gdriveplayer",
    "gofile", "streamlare", "streamvid", "tubeload",
    "okhd.site", "film77.xyz", "vidoba.org", "larhu.website",
    "mp4plus.org", "anafast.org", "vidspeed.org", "abstream.to",
    "dsvplay.com", "vidbam.org", "vidmoly.to", "streamwish",
    "embedwish", "filelions", "swiftplayer", "vidsrc",
    "turbobit", "nitroflare", "1fichier",
]

BLOCKED_DOWNLOAD_HOSTS = {"ok.ru", "odnoklassniki.ru"}

def is_real_server(url):
    if not url: return False
    url_lower = url.lower()
    return any(host in url_lower for host in REAL_VIDEO_HOSTS)

def is_allowed_download_server(url):
    if not url: return False
    host = urlparse(url).netloc.lower().split(":")[0]
    return host not in BLOCKED_DOWNLOAD_HOSTS and not host.endswith(".ok.ru")

def extract_video_fingerprint(url):
    if not url: return None
    try:
        from urllib.parse import parse_qs
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        parts = [p for p in parsed.path.strip('/').lower().split('/') if p]
        if len(parts) >= 2:
            return f"{netloc}/{parts[0]}/{parts[1]}"
        base = f"{netloc}/{parts[0]}" if parts else netloc
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            id_params = ['id', 'oid', 'v', 'vid', 'file', 'i', 'key', 'uid', 'code']
            id_parts = []
            for p in id_params:
                if p in qs:
                    id_parts.append(f"{p}={qs[p][0].lower()}")
            if id_parts:
                return f"{base}?{'&'.join(id_parts)}"
            first_key = sorted(qs.keys())[0]
            return f"{base}?{first_key}={qs[first_key][0][:40].lower()}"
        return base
    except:
        return None

def is_duplicate_video(watch_links, seen_fingerprints):
    for link in watch_links:
        fp = extract_video_fingerprint(link)
        if fp and fp in seen_fingerprints:
            return True, fp
    return False, None

bot_status = {
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "last_check": None,
    "current_domain": LAROZA_DOMAIN,
    "check_count": 0,
    "series_status": {},
    "total_added": 0,
}

def detect_active_domain(site_label, known_domains, exclude_domains, content_keywords,
                         search_keywords, search_queries, fallback_domain, priority_domain=None):
    print(f"\n[*] جاري التحقق من دومين {site_label} الحالي...")

    def try_domain(domain):
        try:
            r = requests.get(domain, headers=HEADERS, timeout=10, allow_redirects=True)
            if r.status_code != 200: return None
            final = f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"
            if any(ex in final for ex in exclude_domains): return None
            if any(kw in r.text for kw in content_keywords): return final
        except: pass
        return None

    seen = set()
    if priority_domain:
        seen.add(priority_domain)
        result = try_domain(priority_domain)
        if result:
            print(f"[+] الدومين اليدوي شغال ({site_label}): {result}")
            return result
        print(f"[-] الدومين اليدوي {priority_domain} مش شغال، هكمل البحث التلقائي...")

    for d in known_domains:
        if d in seen: continue
        seen.add(d)
        result = try_domain(d)
        if result:
            print(f"[+] الدومين النشط ({site_label}): {result}")
            return result
        print(f"[-] {d} لا يعمل أو ليس {site_label}.")

    print(f"[*] جاري البحث في DuckDuckGo عن دومين {site_label} الحالي...")
    for query in search_queries:
        try:
            req = requests.post("https://lite.duckduckgo.com/lite/", headers=HEADERS, data={"q": query}, timeout=15)
            soup = BeautifulSoup(req.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                parsed = urlparse(href)
                netloc = parsed.netloc.lower()
                if not netloc or not parsed.scheme.startswith('http'): continue
                if any(ex in netloc for ex in exclude_domains): continue
                if any(kw in netloc for kw in search_keywords):
                    candidate = f"{parsed.scheme}://{parsed.netloc}"
                    if candidate in seen: continue
                    seen.add(candidate)
                    result = try_domain(candidate)
                    if result:
                        print(f"[+] تم التقاط دومين {site_label} من DuckDuckGo: {result}")
                        return result
        except Exception as e:
            print(f"[-] فشل البحث بالاستعلام '{query}': {e}")

    print(f"[-] استخدام الدومين الاحتياطي ({site_label}): {fallback_domain}")
    return fallback_domain


def get_current_laroza_domain():
    priority_domain = load_manual_domain()
    return detect_active_domain(
        site_label="لاروزا",
        known_domains=[LAROZA_DOMAIN, *LAROZA_KNOWN_DOMAINS],
        exclude_domains=["youtube.com", "dailymotion.com", "yandex.com", "facebook.com",
                          "twitter.com", "laaroza-tv.com", "cima.laaroza", "larozaa.top"],
        content_keywords=["view-serie", "مسلسل", "حلقة", "larozz", "laroza", "larooza"],
        search_keywords=["larozaa", "larozza", "laroza", "larooza"],
        search_queries=["موقع لاروزا", "larozza site", "laroza مسلسلات"],
        fallback_domain=LAROZA_DOMAIN,
        priority_domain=priority_domain,
    )

def get_infinity_session(url):
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        res = session.get(url, timeout=15)
        if "toNumbers" in res.text and "slowAES.decrypt" in res.text:
            a_match = re.search(r'a=toNumbers\("([a-f0-9]+)"\)', res.text)
            b_match = re.search(r'b=toNumbers\("([a-f0-9]+)"\)', res.text)
            c_match = re.search(r'c=toNumbers\("([a-f0-9]+)"\)', res.text)
            if a_match and b_match and c_match:
                key = binascii.unhexlify(a_match.group(1))
                iv = binascii.unhexlify(b_match.group(1))
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = cipher.decrypt(binascii.unhexlify(c_match.group(1)))
                cookie_val = binascii.hexlify(decrypted).decode('utf-8')
                session.cookies.set('__test', cookie_val, domain=urlparse(url).netloc, path='/')
                session.get(f"{url}?i=1", timeout=15)
    except: pass
    return session

api_session = get_infinity_session(YOUR_API_URL)

def send_telegram_msg(message, chat_id=None):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except: pass

def send_episode_notification(series_name, episode_number, servers):
    watch_links = [link for link in servers.get("watch", []) if link]
    download_links = [link for link in servers.get("download", []) if link]
    lines = [
        "🚀 <b>حلقة جديدة نزلت!</b>",
        "",
        f"🎬 <b>المسلسل:</b> \u200f{escape(series_name)}",
        f"📺 <b>الحلقة:</b> {episode_number}",
    ]

    if watch_links:
        lines.extend(["", "<b>روابط المشاهدة:</b>", *[f"<code>{escape(link)}</code>" for link in watch_links]])
    if download_links:
        lines.extend(["", "<b>روابط التحميل:</b>", *[f"<code>{escape(link)}</code>" for link in download_links]])

    lines.extend(["", "<i>إشعار فقط — لم تتم إضافة الحلقة لقاعدة البيانات.</i>"])
    send_telegram_msg("\n".join(lines))

def save_notification_checkpoint(series_state, state_key, episode_number, video_id, watch_links, seen_fps):
    series_state[state_key] = {
        "last_vid": video_id, "last_ep": episode_number, "notified_ep": episode_number,
        "last_watch_link": watch_links[0] if watch_links else "", "seen_fps": list(seen_fps),
    }
    save_series_state(series_state)

def get_full_url(link, current_domain):
    if not link: return ""
    if link.startswith('http'):
        parsed = urlparse(link)
        link = parsed.path + ("?" + parsed.query if parsed.query else "")
    return f"{current_domain}/{link}" if not link.startswith('/') else f"{current_domain}{link}"

def fetch_page(url, max_hops=8):
    session = requests.Session()
    session.headers.update(HEADERS)
    for _ in range(max_hops):
        try:
            res = session.get(url, timeout=15, allow_redirects=True)
            meta = re.search(r'URL=(https?://[^\"\s>]+)', res.text, re.IGNORECASE)
            if meta and len(res.text) < 1000:
                url = meta.group(1)
                continue
            return res
        except Exception as e:
            print(f"[-] fetch_page error: {e}")
            return None
    return None

def resolve_series_domain(series_name, series_info, current_domain):
    candidates = [series_info.get("domain_override"), current_domain, *LAROZA_KNOWN_DOMAINS]
    seen = set()

    for domain in candidates:
        if not domain or domain in seen: continue
        seen.add(domain)
        try:
            page_url = get_full_url(series_info["url"], domain)
            response = fetch_page(page_url)
            if response and response.status_code == 200:
                if series_name in response.text or "video.php?vid=" in response.text:
                    if domain != current_domain:
                        print(f"  [🌐] {series_name}: استخدام دومين لاروزا {domain}")
                    return domain
        except Exception as e:
            print(f"  [-] تعذر فحص دومين {domain} للمسلسل {series_name}: {e}")

    return current_domain

def build_status_message():
    uptime_seconds = int(time.time() - start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    lines = [
        "✅ <b>البوت شغّال وتمام!</b>",
        "",
        f"⏱ <b>وقت التشغيل:</b> {hours}س {minutes}د {seconds}ث",
        f"🕐 <b>بدأ في:</b> {bot_status['started_at']}",
        f"🔍 <b>آخر فحص:</b> {bot_status['last_check'] or 'جاري...'}",
        f"🌐 <b>الدومين الحالي:</b> {bot_status['current_domain']}" + (" (يدوي 🔒)" if load_manual_domain() else " (تلقائي)"),
        f"🔄 <b>دورات الفحص:</b> {bot_status['check_count']} | ➕ <b>إجمالي الإضافات:</b> {bot_status['total_added']}",
        "",
        "📺 <b>آخر حالة للمسلسلات:</b>",
    ]

    if bot_status["series_status"]:
        for name, info in bot_status["series_status"].items():
            lines.append(f"  • {name}: حلقة {info.get('last_ep', '?')} — {info.get('status', '...')}")
    else: lines.append("  (لم يتم الفحص بعد)")
    return "\n".join(lines)

_pending_setdomain_chats = set()
_pending_restore_chats = set()
_addseries_states = {}  

def _apply_manual_domain(new_domain, chat_id):
    new_domain = new_domain.strip().rstrip("/")
    if not new_domain.startswith("http"): new_domain = "https://" + new_domain
    save_manual_domain(new_domain)
    print(f"[*] تم تحديد دومين يدوي: {new_domain}")
    send_telegram_msg(f"✅ تم تحديد الدومين:\n<code>{new_domain}</code>\n\nهيتجرب فورًا كأولوية.\nلإلغائه: /cleardomain", chat_id=chat_id)

def _apply_restore(json_text, chat_id):
    try:
        new_data = json.loads(json_text)
        if not isinstance(new_data, dict): raise ValueError("البيانات يجب أن تكون JSON Object.")
        current_state = load_series_state()
        for key, val in new_data.items():
            if key in current_state and isinstance(val, dict): current_state[key].update(val)
            else: current_state[key] = val
        save_series_state(current_state)
        print("[*] تم استعادة النسخة الاحتياطية بنجاح.")
        send_telegram_msg("✅ تم استعادة وتحديث بيانات المسلسلات بنجاح!", chat_id=chat_id)
    except Exception as e:
        print(f"[-] فشل الاستعادة: {e}")
        send_telegram_msg(f"❌ فشل في الاستعادة! تأكد من كود الـ JSON.\nالخطأ: {e}", chat_id=chat_id)

def get_last_ep_from_api(series_id):
    global api_session
    try:
        req_api = api_session.post(
            YOUR_API_URL,
            data={"secret_key": SECRET_KEY, "action": "get_latest", "series_id": series_id},
            timeout=15,
        )
        if "aes.js" in req_api.text or "cookie" in req_api.text:
            api_session = get_infinity_session(YOUR_API_URL)
            req_api = api_session.post(
                YOUR_API_URL,
                data={"secret_key": SECRET_KEY, "action": "get_latest", "series_id": series_id},
                timeout=15,
            )
        api_data = json.loads(req_api.text.strip())
        
        last_ep_val = api_data.get('last_ep', 0)
        last_ep_val = int(last_ep_val) if last_ep_val is not None else 0
        last_link_val = api_data.get('last_link', '')
        last_link_val = last_link_val if last_link_val is not None else ''
        
        return last_ep_val, last_link_val
    except Exception as e:
        print(f"[-] فشل جلب آخر حلقة من الـ API للمسلسل {series_id}: {e}")
        return 0, ''

def telegram_commands_listener():
    global TARGET_SERIES
    offset = None
    print("[*] مستمع أوامر التيليجرام جاهز.")
    while True:
        try:
            params = {"timeout": 30}
            if offset: params["offset"] = offset
            
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params=params, timeout=35)
            data = resp.json()
            
            if not data.get("ok"):
                print(f"[-] خطأ من API تيليجرام: {data}")
                time.sleep(5)
                continue
                
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    
                    text = msg.get("text", "")
                    if not isinstance(text, str) or not text.strip(): continue
                        
                    text = text.strip()
                    chat_id = msg.get("chat", {}).get("id")

                    if text == "/cancel":
                        if chat_id in _addseries_states:
                            del _addseries_states[chat_id]
                            send_telegram_msg("❌ تم إلغاء عملية إضافة المسلسل.", chat_id=chat_id)
                        else:
                            send_telegram_msg("⚠️ ليس هناك أي عملية جارية للإلغاء.", chat_id=chat_id)
                        continue

                    if chat_id in _addseries_states:
                        state = _addseries_states[chat_id]
                        if state["step"] == "name":
                            state["name"] = text
                            state["step"] = "url"
                            send_telegram_msg(f"✅ اسم المسلسل: <b>{text}</b>\n\n2️⃣ <b>ابعت دلوقتي مسار المسلسل على لاروزا:</b>\n(مثال: /view-serie1.php?ser=123)", chat_id=chat_id)
                        
                        elif state["step"] == "url":
                            text_url = text.strip()
                            if text_url.startswith("http"):
                                try:
                                    parsed_url = urlparse(text_url)
                                    text_url = parsed_url.path + ("?" + parsed_url.query if parsed_url.query else "")
                                except: pass
                            state["url"] = text_url
                            state["step"] = "id"
                            send_telegram_msg(f"✅ المسار اتسجل: <b>{text_url}</b>\n\n3️⃣ ممتاز، آخر خطوة: <b>ابعت الـ ID بتاع المسلسل في موقعك</b>\n(اكتب رقم بس، مثال: 50):", chat_id=chat_id)
                        
                        elif state["step"] == "id":
                            try:
                                s_id = int(text)
                                s_name = state["name"]
                                s_url = state["url"]
                                
                                TARGET_SERIES[s_name] = {"url": s_url, "db_id": s_id}
                                save_target_series(TARGET_SERIES)
                                
                                send_telegram_msg("⏳ جاري الاتصال بموقعك لمعرفة آخر حلقة مرفوعة...", chat_id=chat_id)
                                last_ep, last_link = get_last_ep_from_api(s_id)
                                
                                series_state = load_series_state()
                                series_state[str(s_id)] = {
                                    "last_ep": last_ep,
                                    "last_watch_link": last_link,
                                    "seen_fps": [],
                                    "last_vid": None
                                }
                                save_series_state(series_state)

                                bot_status["series_status"][s_name] = {
                                    "last_ep": last_ep, 
                                    "status": "⏳ في انتظار بدء دورة الفحص القادمة..."
                                }

                                ep_text = f"{last_ep}" if last_ep > 0 else "0 (مسلسل جديد - سيبدأ البحث عن الحلقة 1)"

                                success_msg = (
                                    f"🎉 <b>تم إضافة المسلسل بنجاح!</b>\n\n"
                                    f"🎬 <b>الاسم:</b> {s_name}\n"
                                    f"🔗 <b>المسار:</b> {s_url}\n"
                                    f"🆔 <b>الأي دي:</b> {s_id}\n\n"
                                    f"📊 <b>آخر حلقة في موقعك:</b> {ep_text}\n\n"
                                    f"<i>هيبدأ المراقبة من بعد الحلقة دي فوراً.</i>"
                                )
                                send_telegram_msg(success_msg, chat_id=chat_id)
                                del _addseries_states[chat_id]
                            except ValueError:
                                send_telegram_msg("❌ الأي دي لازم يكون <b>رقم صحيح فقط</b>! جرب تاني، ابعت الرقم:", chat_id=chat_id)
                        continue

                    if text.startswith("/status"):
                        send_telegram_msg(build_status_message(), chat_id=chat_id)

                    elif text.startswith("/backup"):
                        try:
                            state = load_series_state()
                            if not state: send_telegram_msg("⚠️ لا يوجد بيانات حالية.", chat_id=chat_id)
                            else:
                                json_str = json.dumps(state, indent=2, ensure_ascii=False)
                                if len(json_str) < 4000:
                                    send_telegram_msg(f"📦 <b>النسخة الاحتياطية (الحالة المحلية):</b>\n<pre>{escape(json_str)}</pre>", chat_id=chat_id)
                                else:
                                    with open(STATE_FILE, 'rb') as f:
                                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument", data={"chat_id": chat_id, "caption": "📦 ملف النسخة الاحتياطية"}, files={"document": f}, timeout=15)
                        except Exception as e: send_telegram_msg(f"❌ خطأ: {e}", chat_id=chat_id)

                    elif text.startswith("/restore"):
                        parts = text.split(maxsplit=1)
                        if len(parts) > 1:
                            _pending_restore_chats.discard(chat_id)
                            _apply_restore(parts[1], chat_id)
                        else:
                            _pending_restore_chats.add(chat_id)
                            send_telegram_msg("📩 أرسل كود JSON الآن لاستعادة النسخة الاحتياطية\n(لإلغاء العملية ارسل /cancel):", chat_id=chat_id)

                    elif text.startswith("/addseries"):
                        _addseries_states[chat_id] = {"step": "name"}
                        send_telegram_msg("📝 <b>إضافة مسلسل جديد للمراقبة</b>\n<i>(لو حابب تلغي العملية في أي وقت ابعت /cancel)</i>\n\n1️⃣ <b>ابعت اسم المسلسل دلوقتي:</b>", chat_id=chat_id)

                    elif text.startswith("/removeseries"):
                        s_name = text.replace("/removeseries", "").strip()
                        if not s_name:
                            send_telegram_msg("⚠️ اكتب اسم المسلسل بعد الأمر.\nمثال:\n<code>/removeseries حب ع ورق</code>", chat_id=chat_id)
                        elif s_name in TARGET_SERIES:
                            db_id = TARGET_SERIES[s_name]['db_id']
                            del TARGET_SERIES[s_name]
                            save_target_series(TARGET_SERIES)
                            
                            state = load_series_state()
                            if str(db_id) in state:
                                del state[str(db_id)]
                                save_series_state(state)

                            send_telegram_msg(f"🗑 تم حذف المسلسل: <b>{s_name}</b> وإزالة بياناته من الذاكرة.", chat_id=chat_id)
                        else:
                            send_telegram_msg(f"❌ المسلسل ( {s_name} ) مش موجود في القائمة.", chat_id=chat_id)

                    elif text.startswith("/listseries"):
                        if not TARGET_SERIES:
                            send_telegram_msg("📂 القائمة فاضية مفيش مسلسلات بتراقبها حالياً.", chat_id=chat_id)
                        else:
                            msg = "📋 <b>قائمة المسلسلات الحالية:</b>\n\n"
                            state = load_series_state()
                            for k, v in TARGET_SERIES.items():
                                ep_info = state.get(str(v['db_id']), {}).get('last_ep', '?')
                                msg += f"• <b>{k}</b> (ID: {v['db_id']}) - آخر حلقة مسجلة: {ep_info}\n"
                            send_telegram_msg(msg, chat_id=chat_id)

                    elif text.startswith("/setdomain"):
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2 or not parts[1].strip():
                            _pending_setdomain_chats.add(chat_id)
                            send_telegram_msg("📩 ابعت لينك الدومين الجديد دلوقتي", chat_id=chat_id)
                        else:
                            _pending_setdomain_chats.discard(chat_id)
                            _apply_manual_domain(parts[1], chat_id)

                    elif text.startswith("/cleardomain"):
                        _pending_setdomain_chats.discard(chat_id)
                        clear_manual_domain()
                        send_telegram_msg("✅ تم إلغاء الدومين اليدوي، البوت هيعتمد على الاكتشاف التلقائي", chat_id=chat_id)

                    elif chat_id in _pending_restore_chats:
                        _pending_restore_chats.discard(chat_id)
                        _apply_restore(text, chat_id)

                    elif chat_id in _pending_setdomain_chats:
                        _pending_setdomain_chats.discard(chat_id)
                        _apply_manual_domain(text, chat_id)
        except Exception as e:
            print(f"[-] خطأ غير متوقع في مستمع الأوامر: {e}")
            time.sleep(5)


def extract_servers(episode_url, current_domain):
    data = {"is_real": False, "watch": ["", "", "", ""], "download": ["", ""]}
    try:
        vid_match = re.search(r'vid=([a-zA-Z0-9]+)', episode_url)
        if not vid_match: return data
        vid = vid_match.group(1)

        play_url = f"{current_domain}/play.php?vid={vid}"
        req_play = requests.get(play_url, headers=HEADERS, timeout=15)
        soup_play = BeautifulSoup(req_play.text, 'html.parser')
        site_netloc = urlparse(current_domain).netloc.lower()
        known_laroza_hosts = {urlparse(domain).netloc.lower() for domain in LAROZA_KNOWN_DOMAINS}

        def get_external_link(href):
            if not href: return None
            href = href.strip()
            if href.startswith("//"): href = f"https:{href}"
            if not href.startswith(("http://", "https://")): return None
            link_netloc = urlparse(href).netloc.lower()
            normalized_host = re.sub(r"[^a-z0-9]", "", link_netloc)
            if link_netloc == site_netloc or link_netloc in known_laroza_hosts or any(variant in normalized_host for variant in ("laroza", "larooza", "larozza", "larroza")): return None
            return href

        watch_servers = []
        for li in soup_play.find_all('li', attrs={"data-embed-url": True}):
            embed_url = get_external_link(li.get('data-embed-url', ''))
            if embed_url: watch_servers.append(embed_url)

        if not watch_servers:
            for ifr in soup_play.find_all('iframe'):
                src = get_external_link(ifr.get('src', ''))
                if src and is_real_server(src): watch_servers.append(src)

        if watch_servers:
            data["is_real"] = True
            for i in range(4): data["watch"][i] = watch_servers[i] if i < len(watch_servers) else watch_servers[-1]

        dl_url = f"{current_domain}/download.php?vid={vid}"
        req_dl = requests.get(dl_url, headers=HEADERS, timeout=15)
        soup_dl = BeautifulSoup(req_dl.text, 'html.parser')

        download_links = []
        for a in soup_dl.find_all('a'):
            href = get_external_link(a.get('href', ''))
            if href and is_allowed_download_server(href) and 'cdn' not in href.lower():
                if href not in download_links: download_links.append(href)

        for i in range(2):
            if i < len(download_links): data["download"][i] = download_links[i]
    except Exception as e:
        print(f"[-] خطأ في استخراج السيرفرات: {e}")
    return data

def find_next_ep_from_vid(vid, current_domain, min_ep=0):
    try:
        url = f"{current_domain}/video.php?vid={vid}"
        res = fetch_page(url)
        if not res: return None
        soup = BeautifulSoup(res.text, 'html.parser')
        candidates = []
        for a in soup.find_all('a'):
            text = a.text.strip()
            href = a.get('href', '')
            if not href or 'video' not in href.lower(): continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m: continue
            
            if 'التالية' in text:
                ep_m = re.search(r'(\d+)', text)
                if ep_m and int(ep_m.group(1)) > min_ep:
                    return {'vid': vid_m.group(1), 'num': int(ep_m.group(1)), 'url': get_full_url(href, current_domain)}
            ep_m = re.search(r'^(\d+)\s*حلق[ةه]$|^حلق[ةه]\s*(\d+)$', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                if ep_num > min_ep: candidates.append({'vid': vid_m.group(1), 'num': ep_num, 'url': get_full_url(href, current_domain)})
        if candidates: return min(candidates, key=lambda x: x['num'])
    except: pass
    return None

def find_vid_for_ep(series_info, last_ep, current_domain):
    season_keyword = series_info.get('season_keyword')
    series_url = get_full_url(series_info['url'], current_domain)
    try:
        res = fetch_page(series_url)
        if not res: return None
        soup = BeautifulSoup(res.text, 'html.parser')
        seen_vids = set()
        all_eps = []
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.text.strip()
            if not href or 'video' not in href.lower(): continue
            if season_keyword and season_keyword not in text: continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m: continue
            vid_id = vid_m.group(1)
            if vid_id in seen_vids: continue
            ep_m = re.search(r'(\d+)\s*حلق[ةه]|(?:ال)?حلق[ةه]\s*(\d+)', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                seen_vids.add(vid_id)
                if last_ep == 0: all_eps.append((ep_num, vid_id))
                elif ep_num == last_ep: return vid_id
        if last_ep == 0 and all_eps:
            all_eps.sort(key=lambda x: x[0])
            return all_eps[0][1]
    except: pass
    return None

def find_all_eps_on_page(series_info, current_domain):
    season_keyword = series_info.get('season_keyword')
    series_url = get_full_url(series_info['url'], current_domain)
    results = []
    try:
        res = fetch_page(series_url)
        if not res: return results
        soup = BeautifulSoup(res.text, 'html.parser')
        seen_vids = set()
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.text.strip()
            if not href or 'video' not in href.lower(): continue
            if season_keyword and season_keyword not in text: continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m: continue
            vid_id = vid_m.group(1)
            if vid_id in seen_vids: continue
            ep_m = re.search(r'(\d+)\s*حلق[ةه]|(?:ال)?حلق[ةه]\s*(\d+)', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                results.append({'vid': vid_id, 'num': ep_num, 'url': get_full_url(href, current_domain)})
                seen_vids.add(vid_id)
        results.sort(key=lambda x: x['num'])
    except: pass
    return results

_domain_cache = {"domain": None, "cycle": 0}
DOMAIN_RECHECK_EVERY = 20  

def run_bot():
    global api_session, LAROZA_DOMAIN, TARGET_SERIES

    bot_status["check_count"] += 1
    cycle = bot_status["check_count"]
    manual_domain = load_manual_domain()
    
    if manual_domain and manual_domain != _domain_cache.get("manual_seen"):
        print(f"\n[*] دومين يدوي جديد اتحدد ({manual_domain}) — جاري التحقق فورًا...")
        _domain_cache["domain"] = None

    _domain_cache["manual_seen"] = manual_domain

    if _domain_cache["domain"] is None or cycle % DOMAIN_RECHECK_EVERY == 1:
        current_domain = get_current_laroza_domain()
        _domain_cache["domain"] = current_domain
        _domain_cache["cycle"] = cycle
    else:
        current_domain = _domain_cache["domain"]
        print(f"\n[*] استخدام الدومين المحفوظ: {current_domain} (دورة {cycle})")

    LAROZA_DOMAIN = current_domain
    bot_status["current_domain"] = current_domain
    series_state = load_series_state()
    print(f"\n=== الفحص الآن باستخدام الدومين: {current_domain} ===")

    for series_name, series_info in list(TARGET_SERIES.items()):
        db_id = series_info.get('db_id')
        if not db_id: continue
        state_key = str(db_id)
        series_domain = resolve_series_domain(series_name, series_info, current_domain)

        stored_data = series_state.get(state_key, {})
        
        last_ep = stored_data.get('last_ep', 0)
        last_ep = int(last_ep) if last_ep is not None else 0
        current_last_link = stored_data.get('last_watch_link', "") or ""

        bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "جاري الفحص..."}
        print(f"\n[*] {series_name} | آخر حلقة محلياً: {last_ep} | جاري البحث...")

        current_vid = stored_data.get('last_vid')
        stored_ep   = stored_data.get('last_ep', -1)
        stored_ep   = int(stored_ep) if stored_ep is not None else -1
        
        notified_ep = stored_data.get('notified_ep', stored_ep)
        notified_ep = int(notified_ep) if notified_ep is not None else stored_ep
        
        seen_fps_raw = stored_data.get('seen_fps', [])
        seen_fps    = set(seen_fps_raw if seen_fps_raw is not None else [])

        if NOTIFY_ONLY and notified_ep > last_ep:
            last_ep = notified_ep
            current_last_link = stored_data.get('last_watch_link', current_last_link) or ""

        if last_ep == 0:
            print(f"  [★] مسلسل جديد {series_name} — جاري البحث في الصفحة...")
            all_page_eps = find_all_eps_on_page(series_info, series_domain)
            if not all_page_eps:
                bot_status["series_status"][series_name] = {"last_ep": 0, "status": "⏳ لا حلقات بعد"}
                continue

            if NOTIFY_ONLY:
                latest_item = all_page_eps[-1]
                series_state[state_key] = {
                    "last_vid": latest_item["vid"], "last_ep": latest_item["num"], "notified_ep": latest_item["num"],
                    "last_watch_link": "", "seen_fps": [],
                }
                save_series_state(series_state)
                bot_status["series_status"][series_name] = {"last_ep": latest_item['num'], "status": f"📌 تم ضبط نقطة البداية عند ح{latest_item['num']}"}
                continue

            added_any = False
            total_eps = len(all_page_eps)
            for idx, ep_item in enumerate(all_page_eps):
                ep_num, ep_vid, ep_url = ep_item['num'], ep_item['vid'], ep_item['url']
                servers = extract_servers(ep_url, series_domain)
                w, d = servers["watch"], servers["download"]
                all_w, all_dl = [s for s in w if s], [s for s in d if s]

                if not w[0] or not any(is_real_server(s) for s in all_w): continue
                if is_duplicate_video(all_w, seen_fps)[0]: continue
                if (idx == total_eps - 1) and not any(is_real_server(s) for s in all_dl): break

                insert_payload = {
                    "secret_key": SECRET_KEY, "action": "insert", "series_id": db_id, "title": f"الحلقة {ep_num}",
                    "episode_number": ep_num, "watch_link": w[0], "watch_link_2": w[1], "watch_link_3": w[2], "watch_link_4": w[3],
                    "download_link": d[0], "download_link_2": d[1],
                }
                try:
                    ins = api_session.post(YOUR_API_URL, data=insert_payload, timeout=20)
                    if "aes.js" in ins.text or "cookie" in ins.text:
                        api_session = get_infinity_session(YOUR_API_URL)
                        ins = api_session.post(YOUR_API_URL, data=insert_payload, timeout=20)

                    if "INSERTED" in ins.text:
                        bot_status["total_added"] += 1
                        last_ep = ep_num
                        current_vid = ep_vid
                        for lnk in all_w:
                            fp = extract_video_fingerprint(lnk)
                            if fp: seen_fps.add(fp)
                        series_state[state_key] = {'last_vid': current_vid, 'last_ep': last_ep, 'seen_fps': list(seen_fps), 'last_watch_link': w[0]}
                        save_series_state(series_state)
                        send_telegram_msg(f"🚀 <b>حلقة جديدة نزلت!</b>\n\n🎬 <b>المسلسل:</b> \u200f{series_name}\n📺 <b>الحلقة:</b> {ep_num}\n\n<i>تم الإضافة بنجاح ✅</i>")
                        added_any = True
                        time.sleep(2)
                except: pass

            bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": f"✅ أُضيفت {last_ep} حلقة" if added_any else "⏳ لا حلقات جاهزة بعد"}
            continue

        if not current_vid:
            current_vid = find_vid_for_ep(series_info, last_ep, series_domain)
            if current_vid:
                series_state[state_key]['last_vid'] = current_vid
                save_series_state(series_state)
            else:
                bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "لم يتم تحديد الحلقة"}
                continue

        real_current_link = current_last_link
        try:
            for _ in range(10): 
                candidate = find_next_ep_from_vid(current_vid, series_domain, min_ep=last_ep)
                if not candidate:
                    all_page_eps = find_all_eps_on_page(series_info, series_domain)
                    next_on_page = [e for e in all_page_eps if e['num'] > last_ep]
                    if next_on_page: candidate = min(next_on_page, key=lambda x: x['num'])
                    else:
                        bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "لا جديد"}
                        break

                target_ep, target_vid, target_url = candidate['num'], candidate['vid'], candidate['url']
                if target_ep <= last_ep or target_vid == current_vid:
                    current_vid = target_vid
                    continue

                confirmation = find_next_ep_from_vid(target_vid, series_domain, min_ep=target_ep)
                servers = extract_servers(target_url, series_domain)
                new_watch_link = servers["watch"][0]

                if not new_watch_link: break
                all_watch = [s for s in servers["watch"] if s]
                all_dl    = [s for s in servers["download"] if s]

                if real_current_link and new_watch_link == real_current_link: break
                if not any(is_real_server(s) for s in all_watch): break
                if is_duplicate_video(all_watch, seen_fps)[0]: break
                if NOTIFY_ONLY and not confirmation: break
                if not NOTIFY_ONLY and not confirmation and not any(is_real_server(s) for s in all_dl): break

                if NOTIFY_ONLY:
                    send_episode_notification(series_name, target_ep, servers)
                    last_ep = target_ep
                    current_vid = target_vid
                    real_current_link = new_watch_link
                    for link in all_watch:
                        fp = extract_video_fingerprint(link)
                        if fp: seen_fps.add(fp)
                    save_notification_checkpoint(series_state, state_key, last_ep, current_vid, all_watch, seen_fps)
                    if not confirmation: break
                    time.sleep(2)
                    continue

                insert_payload = {
                    "secret_key": SECRET_KEY, "action": "insert", "series_id": db_id, "title": f"الحلقة {target_ep}",
                    "episode_number": target_ep, "watch_link": servers["watch"][0], "watch_link_2": servers["watch"][1],
                    "watch_link_3": servers["watch"][2], "watch_link_4": servers["watch"][3],
                    "download_link": servers["download"][0], "download_link_2": servers["download"][1],
                }

                try:
                    insert_req = api_session.post(YOUR_API_URL, data=insert_payload, timeout=20)
                    if "aes.js" in insert_req.text or "cookie" in insert_req.text:
                        api_session = get_infinity_session(YOUR_API_URL)
                        insert_req = api_session.post(YOUR_API_URL, data=insert_payload, timeout=20)

                    if "INSERTED" in insert_req.text:
                        bot_status["total_added"] += 1
                        bot_status["series_status"][series_name] = {"last_ep": target_ep, "status": f"✅ أُضيفت الحلقة {target_ep}"}
                        send_telegram_msg(f"🚀 <b>حلقة جديدة نزلت!</b>\n\n🎬 <b>المسلسل:</b> \u200f{series_name}\n📺 <b>الحلقة:</b> {target_ep}\n\n<i>تم الإضافة بنجاح ✅</i>")
                        last_ep = target_ep
                        current_last_link = new_watch_link
                        real_current_link = new_watch_link
                        current_vid = target_vid
                        for lnk in all_watch:
                            fp = extract_video_fingerprint(lnk)
                            if fp: seen_fps.add(fp)
                        series_state[state_key] = {'last_vid': current_vid, 'last_ep': last_ep, 'seen_fps': list(seen_fps), 'last_watch_link': current_last_link}
                        save_series_state(series_state)
                        if not confirmation: break
                    else: break
                except: break
                time.sleep(2)
        except Exception as e: 
            bot_status["series_status"][series_name] = {"last_ep": "?", "status": "خطأ"}
            print(f"[-] خطأ أثناء فحص المسلسل {series_name}: {e}")
        time.sleep(5) 

    bot_status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_status()

if __name__ == "__main__":
    start_time = time.time()
    listener_thread = threading.Thread(target=telegram_commands_listener, daemon=True)
    listener_thread.start()
    send_telegram_msg("🚀 <b>سيرفر المراقبة الذكي اشتغل!</b>\n\n- استخدم /addseries لإضافة مسلسل\n- استخدم /removeseries للحذف\n- استخدم /listseries لعرض القائمة")
    while True:
        try: 
            run_bot()
        except Exception as e:
            err_msg = f"[-] خطأ أوقف دورة الفحص الحالية: {e}"
            print(err_msg)
            send_telegram_msg(f"⚠️ <b>تنبيه من النظام:</b> حصل خطأ مفاجئ ووقف دورة الفحص الحالية:\n<code>{e}</code>\nالبوت هيكمل دورته الجاية عادي متقلقش.")
        print("\n[تم الفحص. سيتم الفحص مجدداً بعد 5 دقائق...]")
        time.sleep(300)
