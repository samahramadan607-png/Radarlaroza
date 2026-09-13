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
SERIES_FILE = os.path.join(os.path.dirname(__file__), "..", "bot_target_series.json")
SERIES_BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "series_backups")

series_config_lock = threading.RLock()

def save_status():
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_status, f, ensure_ascii=False)
    except: pass

def load_manual_domain():
    """يحمّل الدومين اليدوي (لو المستخدم حدده بأمر /setdomain) — بيتخطى الاكتشاف التلقائي."""
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
            json.dump(state, f, ensure_ascii=False)
    except: pass


def load_target_series(default=None):
    """يحمّل قائمة المسلسلات التي يديرها البوت من ملف محلي."""
    try:
        with open(SERIES_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else (default or {})
    except:
        return dict(default or {})


def save_target_series():
    """يحفظ قائمة المسلسلات بعد أوامر الإضافة والحذف من تيليجرام."""
    try:
        with series_config_lock:
            with open(SERIES_FILE, "w", encoding="utf-8") as f:
                json.dump(TARGET_SERIES, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[-] فشل حفظ قائمة المسلسلات: {e}")

# ==========================================
# 1. الإعدادات
# ==========================================
YOUR_API_URL = "https://arabfleex.xo.je/api.php" 
SECRET_KEY = "ArabFleex_2024_SecRet"
TELEGRAM_TOKEN = "8692766022:AAEsjS3IrZ3nafTa8WsRu70oKQ2lrsb5tkk"
TELEGRAM_CHAT_ID = "1013251619" 
NOTIFY_ONLY = False  # عند العثور على حلقة جديدة يتم إدراجها في الـ API مباشرة

DEFAULT_TARGET_SERIES = {
}

# القائمة تُقرأ من ملف حتى يمكن إدارتها من خلال أوامر تيليجرام.
TARGET_SERIES = load_target_series(DEFAULT_TARGET_SERIES)

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

# مواقع الفيديو الحقيقية المعروفة — أي سيرفر مش منها يُعتبر وهمي
REAL_VIDEO_HOSTS = [
    "streamtape", "uqload", "doodstream", "dood.", "mp4upload",
    "voe.sx", "filemoon", "ok.ru", "vk.com", "youtube.com",
    "dailymotion", "upvid", "vidoza", "mixdrop", "upstream",
    "supervideo", "videobin", "fembed", "jawcloud", "jetload",
    "hxfile", "vtbe", "vidbom", "vidhide", "playtube",
    "waaw", "wolfstream", "embedgram", "gdriveplayer",
    "gofile", "streamlare", "streamvid", "tubeload",
    # مواقع مستخدمة على لاروزا
    "okhd.site", "film77.xyz", "vidoba.org", "larhu.website",
    "mp4plus.org", "anafast.org", "vidspeed.org", "abstream.to",
    "dsvplay.com", "vidbam.org", "vidmoly.to", "streamwish",
    "embedwish", "filelions", "swiftplayer", "vidsrc",
    "turbobit", "nitroflare", "1fichier",
]

# سيرفرات مسموحة للمشاهدة لكن لا نستخدمها في روابط التحميل.
BLOCKED_DOWNLOAD_HOSTS = {"ok.ru", "odnoklassniki.ru"}

def is_real_server(url):
    """يتحقق إن السيرفر من موقع فيديو حقيقي معروف."""
    if not url:
        return False
    url_lower = url.lower()
    return any(host in url_lower for host in REAL_VIDEO_HOSTS)


def is_allowed_download_server(url):
    if not url:
        return False
    host = urlparse(url).netloc.lower().split(":")[0]
    return host not in BLOCKED_DOWNLOAD_HOSTS and not host.endswith(".ok.ru")


def extract_video_fingerprint(url):
    """
    يستخرج بصمة الفيديو الفريدة (دومين + مسار الـ ID) من رابط الـ embed.
    مثال: https://streamtape.com/e/ABC123/video.mp4 → 'streamtape.com/e/abc123'
    لو عندنا نفس البصمة في حلقتين → الثانية وهمية.
    بعض السيرفرات (زي VK) بتحط معرّف الفيديو في الـ query string مش الـ path،
    فلازم نشملها في البصمة عشان نميّز بين الفيديوهات.
    """
    if not url:
        return None
    try:
        from urllib.parse import parse_qs
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        parts = [p for p in parsed.path.strip('/').lower().split('/') if p]
        if len(parts) >= 2:
            return f"{netloc}/{parts[0]}/{parts[1]}"
        # لو المسار قصير (زي video_ext.php أو embed.php)، المعرّف في الـ query string
        base = f"{netloc}/{parts[0]}" if parts else netloc
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            # نأخذ الـ params اللي تشبه الـ ID (مستقرة ومش session/timestamp)
            id_params = ['id', 'oid', 'v', 'vid', 'file', 'i', 'key', 'uid', 'code']
            id_parts = []
            for p in id_params:
                if p in qs:
                    id_parts.append(f"{p}={qs[p][0].lower()}")
            if id_parts:
                return f"{base}?{'&'.join(id_parts)}"
            # fallback: أول قيمة في الـ query (لو مفيش params معروفة)
            first_key = sorted(qs.keys())[0]
            return f"{base}?{first_key}={qs[first_key][0][:40].lower()}"
        return base
    except:
        return None


def is_duplicate_video(watch_links, seen_fingerprints):
    """
    يتحقق إن أي من روابط المشاهدة بصمتها موجودة مسبقاً في السجل.
    يرجع True لو الفيديو مكرر (وهمي).
    """
    for link in watch_links:
        fp = extract_video_fingerprint(link)
        if fp and fp in seen_fingerprints:
            return True, fp
    return False, None

# ==========================================
# حالة البوت (يتحدث أثناء التشغيل)
# ==========================================
bot_status = {
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "last_check": None,
    "current_domain": LAROZA_DOMAIN,
    "check_count": 0,
    "series_status": {},
    "total_added": 0,
}

# ==========================================
# 2. نظام اصطياد الدومين التلقائي
# ==========================================
def detect_active_domain(site_label, known_domains, exclude_domains, content_keywords,
                          search_keywords, search_queries, fallback_domain, priority_domain=None):
    """
    دالة عامة لاكتشاف الدومين النشط لأي موقع (لاروزا، لاروزا آسيا، ...).
    priority_domain: دومين يدوي (لو المستخدم حدده بـ /setdomain) — بيتجرب الأول قبل أي حاجة تانية،
    لكن لو مش شغال، البحث التلقائي بيكمل عادي على باقي الدومينات المعروفة وDuckDuckGo.
    """
    print(f"\n[*] جاري التحقق من دومين {site_label} الحالي...")

    def try_domain(domain):
        try:
            r = requests.get(domain, headers=HEADERS, timeout=10, allow_redirects=True)
            if r.status_code != 200:
                return None
            final = f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"
            if any(ex in final for ex in exclude_domains):
                return None
            if any(kw in r.text for kw in content_keywords):
                return final
        except:
            pass
        return None

    # أولاً: لو فيه دومين يدوي (priority)، جرّبه قبل أي حاجة
    seen = set()
    if priority_domain:
        seen.add(priority_domain)
        result = try_domain(priority_domain)
        if result:
            print(f"[+] الدومين اليدوي شغال ({site_label}): {result}")
            return result
        print(f"[-] الدومين اليدوي {priority_domain} مش شغال، هكمل البحث التلقائي...")

    # ثانياً: جرّب الدومينات المعروفة بالترتيب
    for d in known_domains:
        if d in seen:
            continue
        seen.add(d)
        result = try_domain(d)
        if result:
            print(f"[+] الدومين النشط ({site_label}): {result}")
            return result
        print(f"[-] {d} لا يعمل أو ليس {site_label}.")

    # ثانياً: بحث في DuckDuckGo كملاذ أخير
    print(f"[*] جاري البحث في DuckDuckGo عن دومين {site_label} الحالي...")
    for query in search_queries:
        try:
            req = requests.post(
                "https://lite.duckduckgo.com/lite/",
                headers=HEADERS,
                data={"q": query},
                timeout=15
            )
            soup = BeautifulSoup(req.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                parsed = urlparse(href)
                netloc = parsed.netloc.lower()
                if not netloc or not parsed.scheme.startswith('http'):
                    continue
                if any(ex in netloc for ex in exclude_domains):
                    continue
                if any(kw in netloc for kw in search_keywords):
                    candidate = f"{parsed.scheme}://{parsed.netloc}"
                    if candidate in seen:
                        continue
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

# ==========================================
# 3. نظام فك التشفير والتواصل
# ==========================================
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

api_session = None


def get_api_session():
    """ينشئ جلسة الـ API عند الحاجة فقط، وليس مع كل دورة فحص."""
    global api_session
    if api_session is None:
        api_session = get_infinity_session(YOUR_API_URL)
    return api_session

def send_telegram_msg(message, chat_id=None):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except: pass


def send_episode_notification(series_name, episode_number, servers):
    """يرسل إشعار الحلقة مع روابط المشاهدة والتحميل المتاحة."""
    watch_links = [link for link in servers.get("watch", []) if link]
    download_links = [link for link in servers.get("download", []) if link]
    lines = [
        "🚀 <b>حلقة جديدة نزلت!</b>",
        "",
        f"🎬 <b>المسلسل:</b> \u200f{escape(series_name)}",
        f"📺 <b>الحلقة:</b> {episode_number}",
    ]

    if watch_links:
        lines.extend([
            "",
            "<b>روابط المشاهدة:</b>",
            *[f"<code>{escape(link)}</code>" for link in watch_links],
        ])
    if download_links:
        lines.extend([
            "",
            "<b>روابط التحميل:</b>",
            *[f"<code>{escape(link)}</code>" for link in download_links],
        ])

    lines.extend(["", "<i>إشعار فقط — لم تتم إضافة الحلقة لقاعدة البيانات.</i>"])
    send_telegram_msg("\n".join(lines))


def save_notification_checkpoint(series_state, state_key, episode_number, video_id, watch_links, seen_fps):
    """يحفظ آخر حلقة تم الإبلاغ عنها محليًا لمنع تكرار الإشعار."""
    series_state[state_key] = {
        "last_vid": video_id,
        "last_ep": episode_number,
        "notified_ep": episode_number,
        "last_watch_link": watch_links[0] if watch_links else "",
        "seen_fps": list(seen_fps),
    }
    save_series_state(series_state)

def get_full_url(link, current_domain):
    if not link: return ""
    return f"{current_domain}/{link}" if not link.startswith('/') else f"{current_domain}{link}"

def fetch_page(url, max_hops=8):
    """جلب صفحة مع اتباع meta-refresh redirects تلقائياً."""
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
    """يختار دومين المسلسل من الدومين الحالي أو أي دومين لاروزا متاح."""
    candidates = [
        series_info.get("domain_override"),
        current_domain,
        *LAROZA_KNOWN_DOMAINS,
    ]
    seen = set()

    for domain in candidates:
        if not domain or domain in seen:
            continue
        seen.add(domain)
        try:
            page_url = get_full_url(series_info["url"], domain)
            response = fetch_page(page_url)
            if response and response.status_code == 200:
                # صفحة لاروزا الصحيحة تحتوي على اسم المسلسل أو روابط حلقاته.
                if series_name in response.text or "video.php?vid=" in response.text:
                    if domain != current_domain:
                        print(f"  [🌐] {series_name}: استخدام دومين لاروزا {domain}")
                    return domain
        except Exception as e:
            print(f"  [-] تعذر فحص دومين {domain} للمسلسل {series_name}: {e}")

    print(f"  [!] لم يتم العثور على دومين مؤكد لـ {series_name} — استخدام {current_domain}")
    return current_domain

# ==========================================
# أمر /status - رد على استعلام الحالة
# ==========================================
def build_status_message():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    lines = [
        "✅ <b>البوت شغّال وتمام!</b>",
        "",
        f"⏱ <b>وقت التشغيل:</b> {hours}س {minutes}د {seconds}ث",
        f"🕐 <b>بدأ في:</b> {bot_status['started_at']}",
        f"🔍 <b>آخر فحص:</b> {bot_status['last_check'] or 'جاري...'}",
        f"🌐 <b>الدومين الحالي:</b> {bot_status['current_domain']}"
        + (" (يدوي 🔒)" if load_manual_domain() else " (تلقائي)"),
        f"🔄 <b>عدد دورات الفحص:</b> {bot_status['check_count']}",
        f"➕ <b>إجمالي الحلقات المضافة:</b> {bot_status['total_added']}",
        "",
        "📺 <b>آخر حالة للمسلسلات:</b>",
    ]

    if bot_status["series_status"]:
        for name, info in bot_status["series_status"].items():
            lines.append(f"  • {name}: حلقة {info.get('last_ep', '?')} — {info.get('status', '...')}")
    else:
        lines.append("  (لم يتم الفحص بعد)")

    return "\n".join(lines)

# ==========================================
# نظام استقبال أوامر التيليجرام
# ==========================================
_pending_setdomain_chats = set()


def normalize_series_url(value):
    """يحوّل رابط صفحة المسلسل إلى مسار نسبي ثابت للتخزين."""
    value = value.strip()
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        path = parsed.path or "/view-serie1.php"
        return path + (f"?{parsed.query}" if parsed.query else "")
    if value.startswith("view-serie1.php"):
        value = "/" + value
    elif value.startswith("ser="):
        value = "/view-serie1.php?" + value
    elif not value.startswith("/"):
        value = "/" + value
    return value


def build_series_list_message():
    with series_config_lock:
        items = list(TARGET_SERIES.items())
    if not items:
        return "📺 مفيش مسلسلات مضافة حاليًا."

    lines = ["📺 <b>المسلسلات المراقبة:</b>", ""]
    for index, (name, info) in enumerate(items, start=1):
        domain = info.get("domain_override")
        suffix = f" — {escape(domain)}" if domain else ""
        lines.append(
            f"{index}. <b>{escape(name)}</b> "
            f"(DB: {info.get('db_id')}){suffix}"
        )
    return "\n".join(lines)


_pending_restore_upload = set()
_pending_restore_data = {}


def build_series_backup_data():
    """ينشئ نسخة JSON من القائمة وحالة المتابعة بدون أي مفاتيح سرية."""
    with series_config_lock:
        series_copy = json.loads(json.dumps(TARGET_SERIES, ensure_ascii=False))
    return {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "series": series_copy,
        "series_state": load_series_state(),
    }


def send_series_backup(chat_id):
    """يحفظ ويرسل نسخة القائمة كملف JSON على تيليجرام."""
    os.makedirs(SERIES_BACKUP_DIR, exist_ok=True)
    filename = f"series_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    file_path = os.path.join(SERIES_BACKUP_DIR, filename)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(build_series_backup_data(), f, ensure_ascii=False, indent=2)

        with open(file_path, "rb") as backup_file:
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={
                    "chat_id": chat_id,
                    "caption": "📦 نسخة احتياطية لقائمة المسلسلات وحالة المتابعة",
                },
                files={
                    "document": (
                        filename,
                        backup_file,
                        "application/json",
                    )
                },
                timeout=30,
            )
        if not response.ok:
            print(f"[-] فشل إرسال النسخة الاحتياطية: {response.text[:200]}")
            send_telegram_msg("❌ حصلت مشكلة أثناء إرسال ملف النسخة الاحتياطية.", chat_id=chat_id)
    except Exception as e:
        print(f"[-] فشل إنشاء النسخة الاحتياطية: {e}")
        send_telegram_msg("❌ حصلت مشكلة أثناء إنشاء النسخة الاحتياطية.", chat_id=chat_id)


def validate_series_backup(raw_data):
    """يتحقق من JSON المرفوع ويعيد قائمة مسلسلات نظيفة وحالة اختيارية."""
    if not isinstance(raw_data, dict):
        raise ValueError("الملف لازم يكون JSON object.")

    raw_series = raw_data.get("series", raw_data)
    if not isinstance(raw_series, dict):
        raise ValueError("قسم series غير موجود أو غير صحيح.")

    clean_series = {}
    for name, info in raw_series.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(info, dict):
            continue
        if not info.get("url") or info.get("db_id") is None:
            continue
        try:
            db_id = int(info["db_id"])
            if db_id <= 0:
                continue
        except (TypeError, ValueError):
            continue

        series_url = normalize_series_url(str(info["url"]))
        if "view-serie" not in series_url or "ser=" not in series_url:
            continue

        clean_info = {"url": series_url, "db_id": db_id}
        domain = info.get("domain_override")
        if domain:
            domain = str(domain).strip().rstrip("/")
            if not domain.startswith(("http://", "https://")):
                domain = "https://" + domain
            clean_info["domain_override"] = domain
        clean_series[name.strip()] = clean_info

    if not clean_series:
        raise ValueError("الملف لا يحتوي على مسلسل صالح.")

    raw_state = raw_data.get("series_state", {})
    clean_state = raw_state if isinstance(raw_state, dict) else {}
    allowed_ids = {str(info["db_id"]) for info in clean_series.values()}
    clean_state = {
        str(key): value
        for key, value in clean_state.items()
        if str(key) in allowed_ids and isinstance(value, dict)
    }
    return clean_series, clean_state


def handle_restore_document(document, chat_id):
    """ينزّل JSON المرفوع ويعرض معاينة قبل استبدال القائمة."""
    if chat_id not in _pending_restore_upload:
        send_telegram_msg(
            "❌ ابدأ أولًا بالأمر <code>/restoreseries</code> ثم ابعت ملف JSON.",
            chat_id=chat_id,
        )
        return True

    try:
        file_id = document.get("file_id")
        file_info = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=20,
        ).json()
        file_path = file_info["result"]["file_path"]
        content_response = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
            timeout=20,
        )
        raw_data = json.loads(content_response.content.decode("utf-8-sig"))
        clean_series, clean_state = validate_series_backup(raw_data)
    except Exception as e:
        print(f"[-] فشل قراءة نسخة المسلسلات: {e}")
        send_telegram_msg(
            "❌ ملف JSON غير صالح أو حصلت مشكلة في تحميله. جرّب ملف backup من البوت.",
            chat_id=chat_id,
        )
        return True

    _pending_restore_upload.discard(chat_id)
    _pending_restore_data[chat_id] = {
        "series": clean_series,
        "series_state": clean_state,
    }
    names = list(clean_series.keys())
    preview = "\n".join(f"• {escape(name)}" for name in names[:20])
    if len(names) > 20:
        preview += f"\n• ... و{len(names) - 20} مسلسل كمان"
    send_telegram_msg(
        f"📥 تم قراءة الملف، فيه <b>{len(names)}</b> مسلسل:\n\n"
        f"{preview}\n\n"
        "لو القائمة صحيحة ابعت <code>/confirmrestore</code> للاستعادة.\n"
        "أو ابعت <code>/cancel</code> للإلغاء.",
        chat_id=chat_id,
    )
    return True


def confirm_series_restore(chat_id):
    pending = _pending_restore_data.pop(chat_id, None)
    if not pending:
        send_telegram_msg(
            "❌ مفيش ملف جاهز للاستعادة. استخدم <code>/restoreseries</code> الأول.",
            chat_id=chat_id,
        )
        return

    with series_config_lock:
        TARGET_SERIES.clear()
        TARGET_SERIES.update(pending["series"])
        save_target_series()
    save_series_state(pending["series_state"])
    bot_status["series_status"] = {}
    send_telegram_msg(
        f"✅ تمت استعادة <b>{len(TARGET_SERIES)}</b> مسلسل وحالة المتابعة بنجاح.",
        chat_id=chat_id,
    )


def initialize_series_state_from_api(db_id):
    """
    يقرأ آخر حلقة من الـ API مرة واحدة عند إضافة مسلسل جديد فقط.
    بعد ذلك لا يتم استخدام get_latest أثناء دورات الفحص.
    """
    global api_session
    try:
        payload = {
            "secret_key": SECRET_KEY,
            "action": "get_latest",
            "series_id": db_id,
        }
        response = get_api_session().post(YOUR_API_URL, data=payload, timeout=15)
        if "aes.js" in response.text or "cookie" in response.text:
            api_session = get_infinity_session(YOUR_API_URL)
            response = api_session.post(YOUR_API_URL, data=payload, timeout=15)

        data = json.loads(response.text.strip())
        last_ep = int(data["last_ep"])
        state = load_series_state()
        state[str(db_id)] = {
            "last_ep": last_ep,
            "last_vid": None,
            "last_watch_link": data.get("last_link", ""),
            "seen_fps": [],
            "initialized_from_api": True,
        }
        save_series_state(state)
        return last_ep
    except Exception as e:
        print(f"[-] تعذر ضبط نقطة بداية المسلسل DB {db_id} من الـ API: {e}")
        return None


_pending_addseries = {}
_pending_delseries = set()


def add_or_update_series(name, series_url, db_id, domain=None):
    """يحفظ مسلسلًا جديدًا ويضبط نقطة بدايته مرة واحدة من الـ API."""
    info = {"url": normalize_series_url(series_url), "db_id": int(db_id)}
    if domain:
        domain = domain.strip().rstrip("/")
        if not domain.startswith(("http://", "https://")):
            domain = "https://" + domain
        info["domain_override"] = domain

    with series_config_lock:
        TARGET_SERIES[name] = info
        save_target_series()

    state = load_series_state()
    state_key = str(db_id)
    if state_key not in state:
        initialized_ep = initialize_series_state_from_api(db_id)
    else:
        initialized_ep = state[state_key].get("last_ep")
    bot_status["series_status"][name] = {
        "last_ep": initialized_ep if initialized_ep is not None else "?",
        "status": "✅ تمت الإضافة — في انتظار أول فحص",
    }
    return info, initialized_ep


def series_added_message(name, info, initialized_ep):
    baseline_text = (
        f"📌 نقطة البداية من موقعك: ح{initialized_ep}"
        if initialized_ep is not None
        else "⚠️ لم أستطع قراءة نقطة البداية من الـ API؛ هتتحدد من لاروزا في أول فحص."
    )
    return (
        f"✅ تمت إضافة/تحديث المسلسل:\n"
        f"🎬 <b>{escape(name)}</b>\n"
        f"🔗 <code>{escape(info['url'])}</code>\n"
        f"🗃 DB ID: <code>{info['db_id']}</code>\n\n"
        f"{baseline_text}\n"
        "بعد كده الفحص هيكون على لاروزا فقط، والـ API هيتكلم عند الحلقة الجديدة فقط."
    )


def handle_pending_series_input(text, chat_id):
    """يتعامل مع خطوات الإضافة والحذف التفاعلية من تيليجرام."""
    if chat_id in _pending_delseries:
        _pending_delseries.discard(chat_id)
        name = text.strip()
        with series_config_lock:
            removed = TARGET_SERIES.pop(name, None)
            if removed:
                save_target_series()
        if not removed:
            send_telegram_msg(f"❌ مش لاقي مسلسل باسم <b>{escape(name)}</b>.", chat_id=chat_id)
            return True
        state = load_series_state()
        state.pop(str(removed.get("db_id")), None)
        save_series_state(state)
        bot_status["series_status"].pop(name, None)
        send_telegram_msg(
            f"✅ تم حذف <b>{escape(name)}</b> من قائمة المراقبة.",
            chat_id=chat_id,
        )
        return True

    pending = _pending_addseries.get(chat_id)
    if not pending:
        return False

    value = text.strip()
    step = pending["step"]
    if step == "name":
        pending["name"] = value
        pending["step"] = "url"
        send_telegram_msg(
            "🔗 ابعت رابط صفحة المسلسل على لاروزا:",
            chat_id=chat_id,
        )
        return True

    if step == "url":
        url = normalize_series_url(value)
        if "view-serie" not in url or "ser=" not in url:
            send_telegram_msg(
                "❌ الرابط مش صحيح. ابعته بالشكل ده:\n"
                "<code>/view-serie1.php?ser=abc123</code>",
                chat_id=chat_id,
            )
            return True
        pending["url"] = url
        pending["step"] = "db_id"
        send_telegram_msg("🗃 ابعت رقم <code>db_id</code> للمسلسل:", chat_id=chat_id)
        return True

    if step == "db_id":
        try:
            db_id = int(value)
            if db_id <= 0:
                raise ValueError
        except ValueError:
            send_telegram_msg("❌ الـ <code>db_id</code> لازم يكون رقمًا موجبًا.", chat_id=chat_id)
            return True
        pending["db_id"] = db_id
        pending["step"] = "domain"
        send_telegram_msg(
            "🌐 لو للمسلسل دومين معين ابعته، أو اكتب <code>تخطي</code>:",
            chat_id=chat_id,
        )
        return True

    if step == "domain":
        domain = "" if value.lower() in {"تخطي", "skip", "-", "لا"} else value
        name = pending["name"]
        info, initialized_ep = add_or_update_series(
            name,
            pending["url"],
            pending["db_id"],
            domain,
        )
        _pending_addseries.pop(chat_id, None)
        send_telegram_msg(
            series_added_message(name, info, initialized_ep),
            chat_id=chat_id,
        )
        return True

    _pending_addseries.pop(chat_id, None)
    return True


def handle_series_command(text, chat_id):
    """ينفّذ أوامر إضافة وحذف وعرض المسلسلات من تيليجرام."""
    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/backupseries", "/backup"}:
        send_telegram_msg("⏳ جاري تجهيز ملف النسخة الاحتياطية...", chat_id=chat_id)
        send_series_backup(chat_id)
        return True

    if command in {"/restoreseries", "/restore"}:
        _pending_restore_upload.add(chat_id)
        _pending_restore_data.pop(chat_id, None)
        send_telegram_msg(
            "📥 ابعت ملف JSON الاحتياطي الآن.\n"
            "استخدم <code>/cancel</code> للإلغاء.",
            chat_id=chat_id,
        )
        return True

    if command == "/confirmrestore":
        confirm_series_restore(chat_id)
        return True

    if command == "/listseries":
        send_telegram_msg(build_series_list_message(), chat_id=chat_id)
        return True

    if command == "/help":
        send_telegram_msg(
            "🛠 <b>أوامر البوت:</b>\n\n"
            "<code>/listseries</code> — عرض المسلسلات\n"
            "<code>/addseries</code> — إضافة مسلسل خطوة بخطوة\n"
            "<code>/remove</code> — حذف مسلسل خطوة بخطوة\n"
            "<code>/backupseries</code> — إرسال نسخة JSON\n"
            "<code>/restoreseries</code> — استعادة نسخة JSON\n"
            "<code>/confirmrestore</code> — تأكيد الاستعادة\n"
            "<code>/setdomain رابط</code> — تحديد دومين يدوي\n"
            "<code>/cleardomain</code> — إلغاء الدومين اليدوي\n"
            "<code>/cancel</code> — إلغاء العملية الحالية\n"
            "<code>/status</code> — حالة البوت\n\n"
            "وتقدر تستخدم الإضافة السريعة أيضًا:\n"
            "<code>/addseries الاسم | الرابط | db_id</code>",
            chat_id=chat_id,
        )
        return True

    if command == "/addseries":
        if not args:
            _pending_addseries[chat_id] = {"step": "name"}
            send_telegram_msg(
                "🎬 اكتب اسم المسلسل الجديد:",
                chat_id=chat_id,
            )
            return True

        fields = [field.strip() for field in args.split("|")]
        if len(fields) not in (3, 4) or not all(fields[:3]):
            send_telegram_msg(
                "❌ الصيغة غير صحيحة.\n\n"
                "استخدم:\n"
                "<code>/addseries الاسم | رابط صفحة المسلسل | db_id | الدومين الاختياري</code>\n\n"
                "مثال:\n"
                "<code>/addseries مسلسل جديد | /view-serie1.php?ser=abc123 | 300</code>",
                chat_id=chat_id,
            )
            return True

        name, series_url, db_id_text = fields[:3]
        try:
            db_id = int(db_id_text)
            if db_id <= 0:
                raise ValueError
        except ValueError:
            send_telegram_msg("❌ قيمة <code>db_id</code> لازم تكون رقمًا صحيحًا موجبًا.", chat_id=chat_id)
            return True

        series_url = normalize_series_url(series_url)
        if "view-serie" not in series_url or "ser=" not in series_url:
            send_telegram_msg(
                "❌ رابط صفحة المسلسل غير صحيح. لازم يكون مثل:\n"
                "<code>/view-serie1.php?ser=abc123</code>",
                chat_id=chat_id,
            )
            return True

        domain = fields[3] if len(fields) == 4 and fields[3] else ""
        info, initialized_ep = add_or_update_series(name, series_url, db_id, domain)
        send_telegram_msg(
            series_added_message(name, info, initialized_ep),
            chat_id=chat_id,
        )
        return True

    if command in {"/delseries", "/removeseries", "/remove", "/delete"}:
        if not args:
            _pending_delseries.add(chat_id)
            send_telegram_msg(
                "🗑 اكتب اسم المسلسل اللي عايز تحذفه:",
                chat_id=chat_id,
            )
            return True

        with series_config_lock:
            removed = TARGET_SERIES.pop(args, None)
            if removed:
                save_target_series()

        if not removed:
            send_telegram_msg(f"❌ مش لاقي مسلسل باسم <b>{escape(args)}</b>.", chat_id=chat_id)
            return True

        state = load_series_state()
        state.pop(str(removed.get("db_id")), None)
        save_series_state(state)
        bot_status["series_status"].pop(args, None)
        send_telegram_msg(
            f"✅ تم حذف <b>{escape(args)}</b> من قائمة المراقبة.\n"
            "البوت مش هيفحصه تاني.",
            chat_id=chat_id,
        )
        return True

    return False


def _apply_manual_domain(new_domain, chat_id):
    new_domain = new_domain.strip().rstrip("/")
    if not new_domain.startswith("http"):
        new_domain = "https://" + new_domain
    save_manual_domain(new_domain)
    print(f"[*] تم تحديد دومين يدوي: {new_domain}")
    send_telegram_msg(
        f"✅ تم تحديد الدومين:\n<code>{new_domain}</code>\n\n"
        f"هيتجرب فورًا كأولوية، والاكتشاف التلقائي هيفضل شغال جنبه — "
        f"لو الدومين ده بطّل شغل يوم من الأيام، البوت هيدور على غيره لوحده تلقائي.\n"
        f"لو عايز تلغيه قبل كده: /cleardomain",
        chat_id=chat_id
    )

def telegram_commands_listener():
    offset = None
    print("[*] مستمع أوامر التيليجرام جاهز. أرسل /status أو /help للتحقق.")
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=35
            )
            data = resp.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip()
                    document = msg.get("document")
                    chat_id = msg.get("chat", {}).get("id")

                    # لا تسمح بتنفيذ أوامر إدارة البوت من أي شات آخر.
                    if str(chat_id) != str(TELEGRAM_CHAT_ID):
                        continue

                    if document:
                        if handle_restore_document(document, chat_id):
                            continue

                    if text.lower() == "/cancel":
                        _pending_addseries.pop(chat_id, None)
                        _pending_delseries.discard(chat_id)
                        _pending_setdomain_chats.discard(chat_id)
                        _pending_restore_upload.discard(chat_id)
                        _pending_restore_data.pop(chat_id, None)
                        send_telegram_msg("✅ تم إلغاء العملية الحالية.", chat_id=chat_id)
                        continue

                    if handle_series_command(text, chat_id):
                        continue

                    if handle_pending_series_input(text, chat_id):
                        continue

                    if text == "/status":
                        print(f"[*] استُقبل أمر /status من {chat_id}")
                        send_telegram_msg(build_status_message(), chat_id=chat_id)

                    elif text.startswith("/setdomain"):
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2 or not parts[1].strip():
                            # مفيش لينك مكتوب مع الأمر — استنى الرسالة الجاية كلينك
                            _pending_setdomain_chats.add(chat_id)
                            send_telegram_msg(
                                "📩 ابعت لينك الدومين الجديد دلوقتي (مثال: https://example.com)",
                                chat_id=chat_id
                            )
                        else:
                            _pending_setdomain_chats.discard(chat_id)
                            _apply_manual_domain(parts[1], chat_id)

                    elif text == "/cleardomain":
                        _pending_setdomain_chats.discard(chat_id)
                        clear_manual_domain()
                        print("[*] تم إلغاء الدومين اليدوي")
                        send_telegram_msg(
                            "✅ تم إلغاء الدومين اليدوي، البوت هيعتمد على الاكتشاف التلقائي بالكامل",
                            chat_id=chat_id
                        )

                    elif chat_id in _pending_setdomain_chats and text:
                        # كنا مستنيين لينك من الشات ده بعد /setdomain
                        _pending_setdomain_chats.discard(chat_id)
                        _apply_manual_domain(text, chat_id)
        except Exception as e:
            print(f"[-] خطأ في مستمع الأوامر: {e}")
            time.sleep(5)

# ==========================================
# 4. استخراج السيرفرات
# ==========================================
def extract_servers(episode_url, current_domain):
    data = {"is_real": False, "watch": ["", "", "", ""], "download": ["", ""]}
    try:
        # استخراج vid من الرابط
        vid_match = re.search(r'vid=([a-zA-Z0-9]+)', episode_url)
        if not vid_match:
            return data
        vid = vid_match.group(1)

        # --- سيرفرات المشاهدة من play.php ---
        play_url = f"{current_domain}/play.php?vid={vid}"
        req_play = requests.get(play_url, headers=HEADERS, timeout=15)
        soup_play = BeautifulSoup(req_play.text, 'html.parser')

        # استخراج نيت-لوك الدومين الحالي لاستخدامه في الفلتر
        site_netloc = urlparse(current_domain).netloc.lower()

        known_laroza_hosts = {
            urlparse(domain).netloc.lower()
            for domain in LAROZA_KNOWN_DOMAINS
        }

        def get_external_link(href):
            """يرجع رابطًا خارجيًا حقيقيًا، ويرفض أي رابط من لاروزا."""
            if not href:
                return None
            href = href.strip()
            if href.startswith("//"):
                href = f"https:{href}"
            if not href.startswith(("http://", "https://")):
                return None

            link_netloc = urlparse(href).netloc.lower()
            normalized_host = re.sub(r"[^a-z0-9]", "", link_netloc)
            is_laroza_host = (
                link_netloc == site_netloc
                or link_netloc in known_laroza_hosts
                or any(
                    variant in normalized_host
                    for variant in ("laroza", "larooza", "larozza", "larroza")
                )
            )
            if is_laroza_host:
                return None
            return href

        watch_servers = []
        # السيرفرات محفوظة في data-embed-url على كل li
        for li in soup_play.find_all('li', attrs={"data-embed-url": True}):
            embed_url = get_external_link(li.get('data-embed-url', ''))
            if embed_url:
                watch_servers.append(embed_url)

        # احتياطي: iframe مباشر — لكن فقط من مواقع فيديو حقيقية
        if not watch_servers:
            for ifr in soup_play.find_all('iframe'):
                src = get_external_link(ifr.get('src', ''))
                if src and is_real_server(src):
                    watch_servers.append(src)

        if watch_servers:
            data["is_real"] = True
            for i in range(4):
                data["watch"][i] = watch_servers[i] if i < len(watch_servers) else watch_servers[-1]

        # --- روابط التحميل من download.php ---
        dl_url = f"{current_domain}/download.php?vid={vid}"
        req_dl = requests.get(dl_url, headers=HEADERS, timeout=15)
        soup_dl = BeautifulSoup(req_dl.text, 'html.parser')

        download_links = []
        for a in soup_dl.find_all('a'):
            href = get_external_link(a.get('href', ''))
            if (
                href
                and is_allowed_download_server(href)
                and 'cdn' not in href.lower()
            ):
                if href not in download_links:
                    download_links.append(href)

        # ناخد أول رابطين مختلفين من روابط التحميل الخارجية المتاحة.
        for i in range(2):
            if i < len(download_links):
                data["download"][i] = download_links[i]

    except Exception as e:
        print(f"[-] خطأ في استخراج السيرفرات: {e}")
    return data

# ==========================================
# 5. دوال مساعدة للنهج التسلسلي
# ==========================================

def find_next_ep_from_vid(vid, current_domain, min_ep=0):
    """يزور صفحة الحلقة ويجيب رابط 'الحلقة التالية'."""
    try:
        url = f"{current_domain}/video.php?vid={vid}"
        res = fetch_page(url)
        if not res:
            return None
        soup = BeautifulSoup(res.text, 'html.parser')
        candidates = []
        for a in soup.find_all('a'):
            text = a.text.strip()
            href = a.get('href', '')
            if not href or 'video' not in href.lower():
                continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m:
                continue
            # حالة 1: رابط صريح "الحلقة التالية"
            if 'التالية' in text:
                ep_m = re.search(r'(\d+)', text)
                if ep_m:
                    ep_num = int(ep_m.group(1))
                    if ep_num > min_ep:
                        return {
                            'vid': vid_m.group(1),
                            'num': ep_num,
                            'url': get_full_url(href, current_domain),
                        }
            # حالة 2: روابط بصيغة "Xحلقة" أو "حلقةX" (بدون كلمة التالية)
            ep_m = re.search(r'^(\d+)\s*حلق[ةه]$|^حلق[ةه]\s*(\d+)$', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                if ep_num > min_ep:
                    candidates.append({
                        'vid': vid_m.group(1),
                        'num': ep_num,
                        'url': get_full_url(href, current_domain),
                    })
        if candidates:
            return min(candidates, key=lambda x: x['num'])
    except Exception as e:
        print(f"[-] خطأ في البحث عن الحلقة التالية من vid={vid}: {e}")
    return None


def find_vid_for_ep(series_info, last_ep, current_domain):
    """
    Fallback: يدور على صفحة المسلسل عن vid الحلقة المطلوبة.
    يعتمد على season_keyword لو موجود (اللعبة ٥ مثلاً).
    لو last_ep == 0 → يرجع vid أقل حلقة موجودة (مسلسل جديد).
    """
    season_keyword = series_info.get('season_keyword')
    series_url = get_full_url(series_info['url'], current_domain)
    try:
        res = fetch_page(series_url)
        if not res:
            return None
        soup = BeautifulSoup(res.text, 'html.parser')
        seen_vids = set()
        all_eps = []
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.text.strip()
            if not href or 'video' not in href.lower():
                continue
            if season_keyword and season_keyword not in text:
                continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m:
                continue
            vid_id = vid_m.group(1)
            if vid_id in seen_vids:
                continue
            ep_m = re.search(r'(\d+)\s*حلق[ةه]|(?:ال)?حلق[ةه]\s*(\d+)', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                seen_vids.add(vid_id)
                if last_ep == 0:
                    all_eps.append((ep_num, vid_id))
                elif ep_num == last_ep:
                    return vid_id
        # مسلسل جديد → أقل حلقة موجودة
        if last_ep == 0 and all_eps:
            all_eps.sort(key=lambda x: x[0])
            return all_eps[0][1]
    except Exception as e:
        print(f"[-] خطأ في البحث عن vid الحلقة {last_ep}: {e}")
    return None


def find_all_eps_on_page(series_info, current_domain):
    """يجمع كل الحلقات المتاحة على صفحة المسلسل ويرجعها مرتبة تصاعدياً."""
    season_keyword = series_info.get('season_keyword')
    series_url = get_full_url(series_info['url'], current_domain)
    results = []
    try:
        res = fetch_page(series_url)
        if not res:
            return results
        soup = BeautifulSoup(res.text, 'html.parser')
        seen_vids = set()
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.text.strip()
            if not href or 'video' not in href.lower():
                continue
            if season_keyword and season_keyword not in text:
                continue
            vid_m = re.search(r'vid=([a-zA-Z0-9]+)', href)
            if not vid_m:
                continue
            vid_id = vid_m.group(1)
            if vid_id in seen_vids:
                continue
            ep_m = re.search(r'(\d+)\s*حلق[ةه]|(?:ال)?حلق[ةه]\s*(\d+)', text)
            if ep_m:
                ep_num = int(ep_m.group(1) or ep_m.group(2))
                results.append({'vid': vid_id, 'num': ep_num, 'url': get_full_url(href, current_domain)})
                seen_vids.add(vid_id)
        results.sort(key=lambda x: x['num'])
    except Exception as e:
        print(f"[-] خطأ في جمع حلقات الصفحة: {e}")
    return results


# ==========================================
# 6. محرك المراقبة (النهج التسلسلي)
# ==========================================
_domain_cache = {"domain": None, "cycle": 0}
DOMAIN_RECHECK_EVERY = 20  # يعيد التحقق من الدومين كل 20 دورة فقط

def run_bot():
    global api_session, LAROZA_DOMAIN

    bot_status["check_count"] += 1
    cycle = bot_status["check_count"]

    # لو حددت دومين يدوي جديد عن طريق /setdomain، اعمل تحقق فوري بدل ما تستنى دورة الكاش
    manual_domain = load_manual_domain()
    if manual_domain and manual_domain != _domain_cache.get("manual_seen"):
        print(f"\n[*] دومين يدوي جديد اتحدد ({manual_domain}) — جاري التحقق فورًا...")
        _domain_cache["domain"] = None  # يفرض إعادة الفحص فورًا

    _domain_cache["manual_seen"] = manual_domain

    # جيب الدومين من الكاش — اعد التحقق كل 20 دورة أو لو مفيش كاش
    # ملاحظة: الاكتشاف التلقائي بيفضل شغال دايمًا، والدومين اليدوي (لو موجود) بيتجرب الأول كـ"أولوية"
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

    # نأخذ نسخة ثابتة حتى لا يسبب /addseries أو /delseries خطأ أثناء الفحص.
    with series_config_lock:
        target_series_snapshot = list(TARGET_SERIES.items())

    for series_name, series_info in target_series_snapshot:
        db_id = series_info['db_id']
        state_key = str(db_id)

        # --- اختيار دومين المسلسل مع تجربة كل دومينات لاروزا ---
        series_domain = resolve_series_domain(series_name, series_info, current_domain)

        # --- تجاهل المسلسلات اللي لسه هتعرض ---
        if series_info.get('pending_air'):
            from datetime import date
            if date.today() < date(2026, 5, 31):
                print(f"  [⏸] {series_name} — لسه هيعرض 31 مايو، تخطّي.")
                bot_status["series_status"][series_name] = {"last_ep": 0, "status": "⏸ ينتظر العرض 31 مايو"}
                continue

        # لا نسأل الـ API عن آخر حلقة في كل دورة.
        # حالة المتابعة المحلية هي المصدر الوحيد أثناء الفحص.
        state_info = series_state.get(state_key, {})
        stored_ep = state_info.get("last_ep")
        current_vid = state_info.get("last_vid")
        current_last_link = state_info.get("last_watch_link", "")
        seen_fps = set(state_info.get("seen_fps", []))

        # أول مرة نراقب فيها مسلسلًا: نحدد أحدث حلقة موجودة كنقطة بداية
        # بدون إرسال أي طلب للـ API أو إضافة حلقات قديمة.
        if stored_ep is None:
            initial_eps = find_all_eps_on_page(series_info, series_domain)
            if not initial_eps:
                print(f"  [!] لا توجد حلقات على لاروزا للمسلسل {series_name}.")
                bot_status["series_status"][series_name] = {
                    "last_ep": 0,
                    "status": "⏳ لا حلقات بعد على لاروزا",
                }
                continue

            latest_item = initial_eps[-1]
            series_state[state_key] = {
                "last_vid": latest_item["vid"],
                "last_ep": latest_item["num"],
                "last_watch_link": "",
                "seen_fps": [],
            }
            save_series_state(series_state)
            bot_status["series_status"][series_name] = {
                "last_ep": latest_item["num"],
                "status": f"📌 تم ضبط نقطة البداية عند ح{latest_item['num']}",
            }
            print(
                f"  [📌] {series_name}: نقطة البداية ح{latest_item['num']} "
                "— لن تتم إضافة الحلقات القديمة."
            )
            continue

        last_ep = int(stored_ep)

        bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "جاري الفحص..."}
        print(f"\n[*] {series_name} | آخر حلقة عندك: {last_ep} | جاري البحث...")


        # لو الـ API أرجع حلقة مختلفة عن المحفوظ، نعيد البحث
        if stored_ep != last_ep or not current_vid:
            current_vid = find_vid_for_ep(series_info, last_ep, series_domain)
            if current_vid:
                series_state[state_key] = {
                    **state_info,
                    'last_vid': current_vid,
                    'last_ep': last_ep,
                }
                save_series_state(series_state)
                print(f"  [~] تم تحديد vid للحلقة {last_ep}: {current_vid}")
            else:
                print(f"  [!] لم يُعثر على vid للحلقة {last_ep} في لاروزا. محاولة لاحقاً.")
                bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "لم يتم تحديد الحلقة"}
                continue

        # عند بدء متابعة مسلسل أو استعادته، ابْنِ بصمة الحلقة الحالية من لاروزا.
        # ده يمنع إضافة الحلقة الوهمية لو أعادت نفس سيرفر الحلقة السابقة.
        if not state_info.get("fingerprints_initialized"):
            try:
                current_episode_url = f"{series_domain}/video.php?vid={current_vid}"
                current_servers = extract_servers(current_episode_url, series_domain)
                current_watch = [link for link in current_servers["watch"] if link]
                for link in current_watch:
                    fingerprint = extract_video_fingerprint(link)
                    if fingerprint:
                        seen_fps.add(fingerprint)
                if current_watch:
                    current_last_link = current_watch[0]
                    state_info["last_watch_link"] = current_last_link
                    state_info["seen_fps"] = list(seen_fps)
                    state_info["fingerprints_initialized"] = True
                    state_info["last_vid"] = current_vid
                    state_info["last_ep"] = last_ep
                    series_state[state_key] = state_info
                    save_series_state(series_state)
                    print(f"  [~] تم تجهيز بصمات الحلقة الحالية ح{last_ep} للمقارنة.")
                else:
                    print(f"  [!] لا توجد بصمة للحلقة الحالية ح{last_ep} — هتتجرب مرة أخرى.")
            except Exception as e:
                print(f"  [!] تعذر تجهيز بصمة الحلقة الحالية: {e}")

        # --- الحلقة الموجودة عندك لا يتم تحديثها أبدًا من لاروزا ---
        # نستخدم رابطها المحفوظ فقط لمنع إضافة نفس السيرفر للحلقة التالية.
        real_current_link = current_last_link

        # --- المشي بالترتيب: حلقة حلقة مع تأكيد وجود التالية ---
        try:
            for _ in range(10):  # حد أقصى 10 حلقات في دورة واحدة

                # الخطوة 1: وجدنا المرشّح للإضافة (last_ep + 1)
                candidate = find_next_ep_from_vid(current_vid, series_domain, min_ep=last_ep)

                # Fallback: لو مالقاش من صفحة الحلقة، امسح صفحة المسلسل كلها
                if not candidate:
                    all_page_eps = find_all_eps_on_page(series_info, series_domain)
                    next_on_page = [e for e in all_page_eps if e['num'] > last_ep]
                    if next_on_page:
                        candidate = min(next_on_page, key=lambda x: x['num'])
                        print(f"  [~] Fallback: وجدنا ح{candidate['num']} من صفحة المسلسل.")
                    else:
                        print(f"  [-] لا يوجد حلقات جديدة.")
                        bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "لا جديد"}
                        break

                target_ep  = candidate['num']
                target_vid = candidate['vid']
                target_url = candidate['url']

                if target_ep <= last_ep or target_vid == current_vid:
                    current_vid = target_vid
                    continue

                # لا ننتظر ظهور حلقة بعدها؛ بمجرد العثور على حلقة جديدة
                # وسيرفر مشاهدة حقيقي نرسلها للـ API فورًا.
                confirmation = True
                print(f"  [+] تم العثور على الحلقة {target_ep} ← فحص السيرفرات ثم الإضافة للـ API...")

                servers = extract_servers(target_url, series_domain)
                new_watch_link = servers["watch"][0]

                if not new_watch_link:
                    print(f"  [!] الحلقة {target_ep} بدون سيرفرات — بانتظار السيرفرات...")
                    bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "⏳ ح" + str(target_ep) + " بدون سيرفر — انتظار"}
                    break

                all_watch = [s for s in servers["watch"] if s]
                all_dl    = [s for s in servers["download"] if s]

                # فحص 1: نفس سيرفر الحلقة السابقة (URL متطابق)؟
                if real_current_link and new_watch_link == real_current_link:
                    print(f"  [!] الحلقة {target_ep} نفس سيرفر السابقة — وهمية، انتظار...")
                    bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "⏳ ح" + str(target_ep) + " وهمية — بانتظار السيرفر الحقيقي"}
                    break

                # فحص 2: هل أي سيرفر من مواقع الفيديو الحقيقية المعروفة؟
                if not any(is_real_server(s) for s in all_watch):
                    print(f"  [!] الحلقة {target_ep} كل سيرفراتها وهمية ({new_watch_link[:60]}) — انتظار...")
                    bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "⏳ ح" + str(target_ep) + " وهمية — بانتظار السيرفر الحقيقي"}
                    break

                # فحص 3: بصمة الفيديو مكررة من حلقة سابقة؟
                dup, dup_fp = is_duplicate_video(all_watch, seen_fps)
                if dup:
                    print(f"  [!] الحلقة {target_ep} بصمة مكررة ({dup_fp}) — وهمية، انتظار...")
                    bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": "⏳ ح" + str(target_ep) + " وهمية (بصمة مكررة)"}
                    break

                # فحص 4: الحلقة بلا تأكيد (الأحدث) وبلا رابط تحميل حقيقي → انتظار
                if NOTIFY_ONLY and not confirmation:
                    print(f"  [⏳] الحلقة {target_ep} الأحدث — انتظار ظهور الحلقة التالية لتأكيدها...")
                    bot_status["series_status"][series_name] = {
                        "last_ep": last_ep,
                        "status": f"⏳ ح{target_ep} بانتظار التأكيد",
                    }
                    break

                if not NOTIFY_ONLY and not confirmation and not any(is_real_server(s) for s in all_dl):
                    print(f"  [⏳] الحلقة {target_ep} الأحدث وبدون رابط تحميل — انتظار التأكيد الكامل...")
                    bot_status["series_status"][series_name] = {"last_ep": last_ep, "status": f"⏳ ح{target_ep} بانتظار التأكيد"}
                    break

                if NOTIFY_ONLY:
                    send_episode_notification(series_name, target_ep, servers)
                    last_ep = target_ep
                    current_vid = target_vid
                    real_current_link = new_watch_link
                    for link in all_watch:
                        fingerprint = extract_video_fingerprint(link)
                        if fingerprint:
                            seen_fps.add(fingerprint)
                    save_notification_checkpoint(
                        series_state,
                        state_key,
                        last_ep,
                        current_vid,
                        all_watch,
                        seen_fps,
                    )
                    bot_status["series_status"][series_name] = {
                        "last_ep": last_ep,
                        "status": f"🔔 تم إرسال إشعار ح{last_ep}",
                    }

                    # لو دي أحدث حلقة، لا نكررها في نفس الدورة.
                    if not confirmation:
                        break
                    time.sleep(2)
                    continue

                insert_payload = {
                    "secret_key": SECRET_KEY, "action": "insert",
                    "series_id": db_id, "title": f"الحلقة {target_ep}",
                    "episode_number": target_ep,
                    "watch_link":   servers["watch"][0],
                    "watch_link_2": servers["watch"][1],
                    "watch_link_3": servers["watch"][2],
                    "watch_link_4": servers["watch"][3],
                    "download_link":   servers["download"][0],
                    "download_link_2": servers["download"][1],
                }

                try:
                    insert_req = get_api_session().post(
                        YOUR_API_URL,
                        data=insert_payload,
                        timeout=20,
                    )
                    if "aes.js" in insert_req.text or "cookie" in insert_req.text:
                        api_session = get_infinity_session(YOUR_API_URL)
                        insert_req = api_session.post(
                            YOUR_API_URL,
                            data=insert_payload,
                            timeout=20,
                        )
                    if "INSERTED" in insert_req.text:
                        print(f"  [✅] نجاح! تم إضافة الحلقة {target_ep}.")
                        bot_status["total_added"] += 1
                        bot_status["series_status"][series_name] = {
                            "last_ep": target_ep,
                            "status": f"✅ أُضيفت الحلقة {target_ep}",
                        }
                        msg = (
                            f"🚀 <b>حلقة جديدة نزلت!</b>\n\n"
                            f"🎬 <b>المسلسل:</b> \u200f{series_name}\n"
                            f"📺 <b>الحلقة:</b> {target_ep}\n\n"
                            f"<i>تم الإضافة بنجاح ✅</i>"
                        )
                        send_telegram_msg(msg)
                        last_ep = target_ep
                        current_last_link = new_watch_link
                        real_current_link = new_watch_link
                        current_vid = target_vid
                        # حفظ بصمات الحلقة المضافة حديثاً
                        for lnk in all_watch:
                            fp = extract_video_fingerprint(lnk)
                            if fp:
                                seen_fps.add(fp)
                        series_state[state_key] = {
                            'last_vid': current_vid,
                            'last_ep': last_ep,
                            'seen_fps': list(seen_fps),
                        }
                        save_series_state(series_state)

                        # لو مفيش حلقة تالية مؤكدة، وقفنا هنا (الأحدث اتضافت)
                        if not confirmation:
                            break
                    else:
                        print(f"  [x] الـ API لم يقبل الإضافة: {insert_req.text[:80]}")
                        break
                except Exception as e:
                    print(f"  [x] خطأ أثناء الرفع: {e}")
                    break

                time.sleep(2)

        except Exception as e:
            print(f"[-] خطأ أثناء فحص {series_name}: {e}")
            bot_status["series_status"][series_name] = {"last_ep": "?", "status": "خطأ"}

        time.sleep(5)  # تأخير بين كل مسلسل وتاني

    bot_status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_status()

if __name__ == "__main__":
    start_time = time.time()

    listener_thread = threading.Thread(target=telegram_commands_listener, daemon=True)
    listener_thread.start()

    send_telegram_msg("🚀 <b>سيرفر المراقبة الذكي اشتغل!</b>\n\nأرسل /status في أي وقت للتحقق من حالة البوت ✅")
    while True:
        try:
            run_bot()
        except Exception as global_e:
            pass
        print("\n[تم الفحص. سيتم الفحص مجدداً بعد دقيقة...]")
        time.sleep(60)
