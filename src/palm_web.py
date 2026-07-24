#!/usr/bin/python3
"""Palm-compatible file catalog and LAN-only gateway administration UI."""

import concurrent.futures
import hmac
import html
import ipaddress
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, abort, redirect, request, send_file, url_for
from werkzeug.utils import secure_filename


CONFIG_PATH = Path("/etc/palm-lap/web.json")
STATE_DIR = Path("/var/lib/palm-web")
STORE = STATE_DIR / "files"
JOBS_FILE = STATE_DIR / "jobs.json"
HELPER = "/usr/local/sbin/palm-web-admin"
ALLOWED_SUFFIXES = {".prc", ".pdb"}
executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="palm-send")
jobs_lock = threading.Lock()
jobs = {}
csrf_token = secrets.token_urlsafe(32)


def esc(value):
    return html.escape(str(value), quote=True)


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def palm_header(path):
    try:
        with path.open("rb") as handle:
            raw = handle.read(78)
        if len(raw) < 68:
            return None
        name = raw[:32].split(b"\0", 1)[0].decode("latin-1", "replace")
        db_type = raw[60:64].decode("ascii", "replace")
        creator = raw[64:68].decode("ascii", "replace")
        return {"name": name or path.stem, "type": db_type, "creator": creator}
    except OSError:
        return None


def managed_files():
    result = []
    for path in sorted(STORE.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in ALLOWED_SUFFIXES:
            result.append({"name": path.name, "size": path.stat().st_size, "header": palm_header(path)})
    return result


def format_size(size):
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def save_jobs():
    temporary = JOBS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(jobs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(JOBS_FILE)


def load_jobs():
    global jobs
    try:
        loaded = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return
        for job in loaded.values():
            if job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job.setdefault("log", []).append("Web service restarted before the job finished.")
        jobs = loaded
        save_jobs()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        jobs = {}


def helper(action, payload=None, timeout=20):
    return subprocess.run(
        ["sudo", "-n", HELPER, action],
        input=json.dumps(payload or {}), text=True, capture_output=True, timeout=timeout,
    )


def update_job(job_id, **values):
    with jobs_lock:
        jobs[job_id].update(values)
        save_jobs()


def append_job_log(job_id, line):
    with jobs_lock:
        log = jobs[job_id].setdefault("log", [])
        log.append(line.rstrip())
        if len(log) > 400:
            del log[:-400]
        jobs[job_id]["updated_at"] = int(time.time())
        save_jobs()


def run_send_job(job_id, filename, address):
    update_job(job_id, status="running", started_at=int(time.time()), updated_at=int(time.time()))
    try:
        process = subprocess.Popen(
            ["sudo", "-n", HELPER, "send"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        process.stdin.write(json.dumps({"filename": filename, "mac": address}))
        process.stdin.close()
        for line in process.stdout:
            append_job_log(job_id, line)
        returncode = process.wait()
        update_job(
            job_id,
            status="complete" if returncode == 0 else "failed",
            returncode=returncode,
            finished_at=int(time.time()), updated_at=int(time.time()),
        )
    except Exception as exc:
        append_job_log(job_id, f"Job runner error: {exc}")
        update_job(job_id, status="failed", returncode=-1,
                   finished_at=int(time.time()), updated_at=int(time.time()))


def start_send(filename, address):
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "kind": "bluetooth-send", "filename": filename,
            "mac": address, "status": "queued", "created_at": int(time.time()),
            "updated_at": int(time.time()), "log": [],
        }
        save_jobs()
    executor.submit(run_send_job, job_id, filename, address)
    return job_id


def page(title, body, refresh=None):
    refresh_tag = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">{refresh_tag}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
body{{font-family:Arial,sans-serif;max-width:980px;margin:24px auto;padding:0 14px;color:#222}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:7px;text-align:left}}
fieldset{{margin:14px 0;padding:12px}}input,select,button{{font-size:1em;padding:5px;margin:3px}}
.ok{{background:#e8f6e8;padding:8px}}.warn{{background:#fff3cd;padding:8px}}.err{{background:#f8d7da;padding:8px}}
.dropzone{{display:block;border:2px dashed #888;border-radius:6px;padding:24px;text-align:center;cursor:pointer;background:#fafafa}}
.dropzone.dragover{{border-color:#1769aa;background:#eaf4ff}}.selected-files{{margin:8px 0;color:#444}}
pre{{white-space:pre-wrap;background:#eee;padding:10px}}nav a{{margin-right:14px}}
</style></head><body><nav><a href="/">Palm files</a><a href="/admin">Administration</a></nav>
<h1>{esc(title)}</h1>{body}</body></html>"""


def portal_page(gateway_name, gateway_ip):
    files = managed_files()
    if files:
        rows = []
        for item in files:
            label = item["header"]["name"] if item["header"] else item["name"]
            rows.append(
                f'<li><a href="{url_for("download", filename=item["name"])}">{esc(label)}</a> '
                f'({esc(format_size(item["size"]))})<br><small>{esc(item["name"])}</small></li>'
            )
        listing = "<ul>" + "".join(rows) + "</ul>"
    else:
        listing = "<p>No Palm files are available.</p>"
    # Deliberately old-browser-friendly markup for Palm WebPro.
    return f"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html><head><title>{esc(gateway_name)} Palm Files</title></head><body bgcolor="#ffffff">
<h1>{esc(gateway_name)} Palm Files</h1><p>Select a file to download and install.</p>{listing}
<hr><p><small>Gateway: {esc(gateway_ip)} &mdash; Bluetooth LAP file service</small></p></body></html>"""


def create_app():
    config = load_config()
    gateway_name = str(config.get("gateway_name", "Palm LAP"))
    gateway_ip = str(config.get("gateway_ip", "10.77.0.1"))
    STORE.mkdir(parents=True, exist_ok=True)
    # Palm files are public through the catalog and must also be readable by
    # the configured operator's OBEX sender. Job/config state stays private.
    STORE.chmod(0o755)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o711)
    load_jobs()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(config.get("max_upload_bytes", 32 * 1024 * 1024))
    max_upload_files = int(config.get("max_upload_files", 50))

    admin_networks = [ipaddress.ip_network(value) for value in config["admin_networks"]]

    def from_admin_network():
        try:
            remote = ipaddress.ip_address(request.remote_addr)
        except ValueError:
            return False
        return any(remote in network for network in admin_networks)

    def admin_required(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not from_admin_network():
                abort(404)
            return function(*args, **kwargs)
        return wrapped

    def require_csrf():
        if not hmac.compare_digest(request.form.get("csrf", ""), csrf_token):
            abort(400, "invalid or expired form token; reload the administration page")

    def csrf():
        return f'<input type="hidden" name="csrf" value="{esc(csrf_token)}">'

    @app.get("/")
    def index():
        return portal_page(gateway_name, gateway_ip)

    @app.get("/health")
    def health():
        return {"status": "ok", "files": len(managed_files())}

    @app.get("/files/<path:filename>")
    def download(filename):
        name = os.path.basename(filename)
        path = (STORE / name).resolve()
        if path.parent != STORE.resolve() or not path.is_file() or path.is_symlink():
            abort(404)
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            abort(404)
        return send_file(path, mimetype="application/vnd.palm", as_attachment=True,
                         download_name=path.name, conditional=True)

    @app.get("/admin")
    @admin_required
    def admin():
        result = helper("status")
        if result.returncode:
            status_block = f'<div class="err">Status failed: {esc(result.stderr)}</div>'
            status = {"devices": [], "authorized": {}, "pairing": {}}
        else:
            status = json.loads(result.stdout)
            pairing = status.get("pairing", {})
            status_block = (
                f'<p class="ok">Pairing window: <b>{esc(pairing.get("pairing window", "unknown"))}</b>; '
                f'{esc(pairing.get("seconds remaining", "0"))} seconds remaining.</p>'
            )
        controller = status.get("controller", {})
        try:
            palm_subnet = ipaddress.ip_network(status.get("subnet", "10.77.0.0/24"))
            local_address = ipaddress.ip_address(status.get("local_ip", "10.77.0.1"))
            used_addresses = {
                ipaddress.ip_address(item["ip"])
                for item in status.get("authorized", {}).values()
            }
            candidates = list(palm_subnet.hosts())
            preferred = ipaddress.ip_address(int(local_address) + 9)
            candidates = [item for item in candidates if int(item) >= int(preferred)] + candidates
            suggested_ip = next(
                str(item) for item in candidates
                if item != local_address and item not in used_addresses
            )
        except (ValueError, KeyError, StopIteration):
            suggested_ip = "10.77.0.10"
        adapter = controller.get("adapter", {})
        services = controller.get("services", {})
        controller_ok = (
            adapter.get("powered") == "yes"
            and adapter.get("lap_uuid") == "yes"
            and services.get("bluetooth") == "active"
            and services.get("palm-lap") == "active"
        )
        controller_class = "ok" if controller_ok else "err"
        controller_summary = (
            f'<div class="{controller_class}"><b>Controller:</b> '
            f'powered {esc(adapter.get("powered", "unknown"))}; '
            f'LAP UUID {esc(adapter.get("lap_uuid", "unknown"))}; '
            f'discoverable {esc(adapter.get("discoverable", "unknown"))}; '
            f'pairable {esc(adapter.get("pairable", "unknown"))}.</div>'
        )
        service_rows = "".join(
            f'<tr><td>{esc(name)}</td><td>{esc(value)}</td></tr>'
            for name, value in services.items()
        ) or '<tr><td colspan="2">Service status unavailable</td></tr>'
        hci_summary = " | ".join(controller.get("hci", [])) or "unavailable"
        faults = controller.get("recent_faults", [])
        fault_block = (
            f'<details><summary>Recent controller fault messages ({len(faults)})</summary>'
            f'<pre>{esc(chr(10).join(faults) or "No matching faults in this boot")}</pre></details>'
        )
        device_options = "".join(
            f'<option value="{esc(item["mac"])}">{esc(item.get("name", item["mac"]))} — {esc(item["mac"])}</option>'
            for item in status.get("devices", [])
        )
        file_options = "".join(
            f'<option value="{esc(item["name"])}">{esc(item["name"])} ({esc(format_size(item["size"]))})</option>'
            for item in managed_files()
        )
        device_rows = "".join(
            f'<tr><td>{esc(item.get("name", ""))}</td><td>{esc(item["mac"])}</td>'
            f'<td>{esc(item.get("connected", "no"))}</td><td>{esc(status.get("authorized", {}).get(item["mac"], {}).get("ip", "not authorized"))}</td></tr>'
            for item in status.get("devices", [])
        ) or '<tr><td colspan="4">No paired devices</td></tr>'
        job_rows = "".join(
            f'<tr><td><a href="/admin/jobs/{esc(job_id)}">{esc(job_id)}</a></td><td>{esc(job.get("filename", ""))}</td><td>{esc(job.get("status", ""))}</td></tr>'
            for job_id, job in sorted(jobs.items(), key=lambda pair: pair[1].get("created_at", 0), reverse=True)[:10]
        ) or '<tr><td colspan="3">No jobs yet</td></tr>'
        file_rows = "".join(
            f'<tr><td><a href="/files/{esc(item["name"])}">{esc(item["name"])}</a></td>'
            f'<td>{esc(format_size(item["size"]))}</td><td><form method="post" action="/admin/delete">{csrf()}'
            f'<input type="hidden" name="filename" value="{esc(item["name"])}">'
            f'<button type="submit">Delete</button></form></td></tr>'
            for item in managed_files()
        ) or '<tr><td colspan="3">No managed files</td></tr>'
        message = request.args.get("message", "")
        message_block = f'<p class="ok">{esc(message)}</p>' if message else ""
        body = f"""{message_block}{controller_summary}{status_block}
<fieldset><legend>Bluetooth diagnostics and recovery</legend>
<p><b>HCI:</b> {esc(hci_summary)}<br><b>Management settings:</b> {esc(controller.get("settings", "unavailable"))}<br>
<b>Device class:</b> {esc(controller.get("class", "unavailable"))}</p>
<table><tr><th>Service</th><th>State</th></tr>{service_rows}</table>{fault_block}
<p><a href="/admin">Refresh diagnostics</a></p>
<form method="post" action="/admin/bluetooth/lap-restart">{csrf()}
<button type="submit">Restart LAP service</button> Re-registers LAP without resetting the controller.</form>
<form method="post" action="/admin/bluetooth/restart" onsubmit="return confirm('Restart Bluetooth and disconnect active Palm sessions?')">{csrf()}
<button type="submit">Restart Bluetooth stack</button> Use when LAP or BlueZ is unhealthy.</form>
<form method="post" action="/admin/bluetooth/uart-reset" onsubmit="return confirm('Perform the deeper Bluetooth UART reset? Active Palm sessions will disconnect.')">{csrf()}
<input type="hidden" name="confirmation" value="RESET"><button type="submit">Reset Bluetooth UART</button>
Use when the controller shows powered no, HCI DOWN, or hardware timeout errors.</form></fieldset>
<fieldset><legend>Upload Palm files</legend><form method="post" action="/admin/upload" enctype="multipart/form-data" id="upload-form">{csrf()}
<label class="dropzone" id="upload-dropzone" for="upload-files"><b>Drop .prc and .pdb files here</b><br>or click to select files</label>
<input type="file" id="upload-files" name="files" multiple required>
<div class="selected-files" id="selected-files">No files selected</div>
<button type="submit">Upload selected files</button> Up to {max_upload_files} files per batch.
<small>The picker intentionally shows every file for iPhone/iPad compatibility; the server accepts only valid .prc and .pdb files.</small></form></fieldset>
<h2>Managed files</h2><table><tr><th>File</th><th>Size</th><th>Action</th></tr>{file_rows}</table>
<fieldset><legend>Send by Bluetooth</legend><p class="warn">The Palm must be awake and discoverable. Sending disconnects its LAP session.</p>
<form method="post" action="/admin/send">{csrf()}<label>Device <select name="mac" required>{device_options}</select></label>
<label>File <select name="filename" required>{file_options}</select></label><button type="submit">Send</button></form></fieldset>
<fieldset><legend>Pair a new Palm</legend><form method="post" action="/admin/pair/open">{csrf()}
<label>Temporary PIN <input name="pin" pattern="[A-Za-z0-9]{{1,16}}" maxlength="16" required></label>
<button type="submit">Open 10-minute pairing window</button></form>
<form method="post" action="/admin/pair/close">{csrf()}<button type="submit">Close pairing window</button></form></fieldset>
<fieldset><legend>Authorize bonded Palm for LAP</legend><form method="post" action="/admin/device/add">{csrf()}
<label>Device <select name="mac" required>{device_options}</select></label><label>Peer IP <input name="ip" value="{esc(suggested_ip)}" required></label>
<label>Name <input name="name" maxlength="80"></label><button type="submit">Authorize</button></form></fieldset>
<h2>Paired devices</h2><table><tr><th>Name</th><th>MAC</th><th>Connected</th><th>LAP address</th></tr>{device_rows}</table>
<h2>Recent send jobs</h2><table><tr><th>Job</th><th>File</th><th>Status</th></tr>{job_rows}</table>
<script>
(function () {{
  var input = document.getElementById('upload-files');
  var zone = document.getElementById('upload-dropzone');
  var selected = document.getElementById('selected-files');
  function showFiles() {{
    var names = [];
    for (var i = 0; i < input.files.length; i++) names.push(input.files[i].name);
    selected.textContent = names.length ? names.length + ' selected: ' + names.join(', ') : 'No files selected';
  }}
  input.addEventListener('change', showFiles);
  zone.addEventListener('dragover', function (event) {{ event.preventDefault(); zone.classList.add('dragover'); }});
  zone.addEventListener('dragleave', function () {{ zone.classList.remove('dragover'); }});
  zone.addEventListener('drop', function (event) {{
    event.preventDefault();
    zone.classList.remove('dragover');
    input.files = event.dataTransfer.files;
    showFiles();
  }});
}}());
</script>"""
        return page(f"{gateway_name} administration", body)

    @app.post("/admin/upload")
    @admin_required
    def upload():
        require_csrf()
        uploads = [item for item in request.files.getlist("files") if item.filename]
        if not uploads:
            # Preserve compatibility with the original single-file form/API.
            uploads = [item for item in request.files.getlist("file") if item.filename]
        if not uploads:
            abort(400, "missing file")
        if len(uploads) > max_upload_files:
            abort(400, f"at most {max_upload_files} files may be uploaded at once")

        staged = []
        seen_names = set()
        try:
            for uploaded in uploads:
                name = secure_filename(uploaded.filename)
                if not name or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                    abort(400, "only .prc and .pdb files are accepted")
                name_key = name.casefold()
                if name_key in seen_names:
                    abort(400, f"duplicate filename in upload: {name}")
                seen_names.add(name_key)
                temporary = STORE / ("." + uuid.uuid4().hex + ".upload")
                uploaded.save(temporary)
                staged.append((temporary, name))

            # Validate the complete batch before making any file visible.
            for temporary, name in staged:
                if temporary.stat().st_size < 78 or palm_header(temporary) is None:
                    abort(400, f"{name} is too small to be a Palm database")
                os.chmod(temporary, 0o644)

            for temporary, name in staged:
                temporary.replace(STORE / name)
        finally:
            for temporary, _name in staged:
                temporary.unlink(missing_ok=True)
        if len(staged) == 1:
            message = f"Uploaded {staged[0][1]}"
        else:
            message = f"Uploaded {len(staged)} files"
        return redirect(url_for("admin", message=message))

    @app.post("/admin/send")
    @admin_required
    def send():
        require_csrf()
        filename = os.path.basename(request.form.get("filename", ""))
        address = request.form.get("mac", "").upper()
        if filename not in {item["name"] for item in managed_files()}:
            abort(400, "unknown file")
        job_id = start_send(filename, address)
        return redirect(url_for("job", job_id=job_id))

    @app.post("/admin/delete")
    @admin_required
    def delete():
        require_csrf()
        filename = os.path.basename(request.form.get("filename", ""))
        path = (STORE / filename).resolve()
        if path.parent != STORE.resolve() or not path.is_file() or path.is_symlink():
            abort(404)
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            abort(400, "unsupported file type")
        path.unlink()
        return redirect(url_for("admin", message=f"Deleted {filename}"))

    @app.get("/admin/jobs/<job_id>")
    @admin_required
    def job(job_id):
        with jobs_lock:
            current = jobs.get(job_id)
            if current is None:
                abort(404)
            snapshot = json.loads(json.dumps(current))
        refresh = 3 if snapshot["status"] in ("queued", "running") else None
        body = (f'<p>Status: <b>{esc(snapshot["status"])}</b></p>'
                f'<p>File: {esc(snapshot.get("filename", ""))}<br>Device: {esc(snapshot.get("mac", ""))}</p>'
                f'<pre>{esc(chr(10).join(snapshot.get("log", [])))}</pre>')
        return page(f'Send job {job_id}', body, refresh=refresh)

    def admin_action(action, payload, success, timeout=30):
        result = helper(action, payload, timeout=timeout)
        if result.returncode:
            return page("Administration error", f'<p class="err">{esc(result.stderr or result.stdout)}</p>'), 400
        return redirect(url_for("admin", message=success))

    @app.post("/admin/pair/open")
    @admin_required
    def pair_open():
        require_csrf()
        return admin_action("pair-open", {"pin": request.form.get("pin", "")}, "Pairing window opened")

    @app.post("/admin/pair/close")
    @admin_required
    def pair_close():
        require_csrf()
        return admin_action("pair-close", {}, "Pairing window closed")

    @app.post("/admin/bluetooth/lap-restart")
    @admin_required
    def lap_restart():
        require_csrf()
        return admin_action("lap-restart", {}, "LAP service restarted")

    @app.post("/admin/bluetooth/restart")
    @admin_required
    def bluetooth_restart():
        require_csrf()
        return admin_action(
            "bluetooth-restart", {}, "Bluetooth stack and LAP service restarted"
        )

    @app.post("/admin/bluetooth/uart-reset")
    @admin_required
    def bluetooth_uart_reset():
        require_csrf()
        return admin_action(
            "bluetooth-uart-reset",
            {"confirmation": request.form.get("confirmation", "")},
            "Bluetooth UART reset started; wait about 20 seconds, then refresh diagnostics",
        )

    @app.post("/admin/device/add")
    @admin_required
    def device_add():
        require_csrf()
        payload = {key: request.form.get(key, "") for key in ("mac", "ip", "name")}
        return admin_action("device-add", payload, "Device authorized for LAP")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8080)
