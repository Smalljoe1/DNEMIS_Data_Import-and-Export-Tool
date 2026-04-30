"""Flask application — replaces dhis2_ajax.php."""
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, render_template, request, session

import db
import dhis2
import export_maps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
# Dynamic mapping is primary. File-based export fallback is optional.
app.config['ENABLE_EXPORT_MAP_FALLBACK'] = (
    os.environ.get('ENABLE_EXPORT_MAP_FALLBACK', 'false').strip().lower()
    in ('1', 'true', 'yes', 'on')
)
db.init_db()

# In-memory server-side auth storage: session_id -> credentials/metadata.
_AUTH_CACHE = {}
_AUTH_TTL_SECONDS = 8 * 60 * 60


def _purge_auth_cache():
    now = datetime.now(timezone.utc).timestamp()
    expired = [k for k, v in _AUTH_CACHE.items() if v.get('expires_at', 0) < now]
    for k in expired:
        _AUTH_CACHE.pop(k, None)


def _set_auth_session(username: str, password: str):
    _purge_auth_cache()
    auth_id = secrets.token_urlsafe(24)
    _AUTH_CACHE[auth_id] = {
        'username': username,
        'password': password,
        'expires_at': datetime.now(timezone.utc).timestamp() + _AUTH_TTL_SECONDS,
    }
    session.clear()
    session['auth_id'] = auth_id
    session.permanent = True


def _clear_auth_session():
    auth_id = session.get('auth_id')
    if auth_id:
        _AUTH_CACHE.pop(auth_id, None)
    session.clear()


def _require_auth_session() -> tuple[str, str]:
    _purge_auth_cache()
    auth_id = session.get('auth_id')
    if not auth_id or auth_id not in _AUTH_CACHE:
        raise ValueError('Session expired. Please sign in again.')
    auth = _AUTH_CACHE[auth_id]
    auth['expires_at'] = datetime.now(timezone.utc).timestamp() + _AUTH_TTL_SECONDS
    return auth['username'], auth['password']


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# AJAX dispatcher — mirrors the PHP dhis2_ajax.php action switch
# ---------------------------------------------------------------------------

@app.route('/ajax', methods=['POST'])
def ajax():
    action = request.form.get('action', '')
    handlers = {
        'login':             _login,
        'logout':            _logout,
        'get_datasets':      _get_datasets,
        'compare':           _compare,
        'save_local_values': _save_local_values,
        'push':              _push,
        'get_sync_logs':     _get_sync_logs,
    }
    handler = handlers.get(action)
    if handler is None:
        return jsonify({'success': False, 'message': f'Unknown action: {action}'})
    try:
        return handler()
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 401:
            return jsonify({'success': False, 'message': 'Invalid username or password.'})
        if status == 403:
            return jsonify({'success': False, 'message': 'Access denied by DHIS2.'})
        return jsonify({'success': False, 'message': f'DHIS2 HTTP error {status}.'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'message': 'Could not reach DHIS2. Check your internet connection.'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': 'DHIS2 request timed out. Please try again.'})
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)})
    except Exception as exc:          # noqa: BLE001
        app.logger.exception('Unhandled error in /ajax action=%s', action)
        return jsonify({'success': False, 'message': f'Server error: {exc}'})


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password are required.'})
    info = dhis2.login(username, password)
    _set_auth_session(username, password)
    return jsonify({'success': True, **info})


def _logout():
    _clear_auth_session()
    return jsonify({'success': True})


def _get_datasets():
    username, password = _require_auth_session()
    datasets = dhis2.get_datasets(username, password)
    return jsonify({'success': True, 'data': datasets})


def _compare():
    username, password = _require_auth_session()
    org_uid     = request.form.get('orgUnitUID', '')
    period      = request.form.get('period', '').strip()
    dataset_uid = request.form.get('dataSetUID', '').strip()

    if not all([org_uid, period, dataset_uid]):
        return jsonify({'success': False, 'message': 'orgUnitUID, period, and dataSetUID are required.'})

    # Fetch dataset structure and both value sets in parallel would be ideal,
    # but requests is synchronous — do them sequentially.
    elements = dhis2.get_dataset_elements(dataset_uid, username, password)
    # Primary fetch using stable UIDs (recommended in DHIS2 docs).
    dhis2_values_uid = dhis2.get_data_values(
        org_uid, period, dataset_uid, username, password, id_scheme='uid'
    )
    local_values = db.get_local_values(org_uid, period)
    # Auto-use export file when present for this dataset (no env var needed).
    dataset_export_map = export_maps.load_dataset_export_map(dataset_uid)
    use_export_map_fallback = dataset_export_map.get('exists', False)

    def _norm_name(val):
        s = (val or '').strip().lower()
        # Collapse internal whitespace to avoid mismatches from formatting differences.
        return re.sub(r'\s+', ' ', s)

    def _norm_coc_name(val):
        s = _norm_name(val)
        if s in ('', 'default', '(default)'):
            return ''
        return s

    def _coc_variants(val):
        """Return normalized COC-name variants for tolerant matching.

        Example: "SS1, Male" -> {"ss1, male", "male"}
        """
        base = _norm_coc_name(val)
        variants = {base}
        if ',' in base:
            tail = base.split(',')[-1].strip()
            if tail:
                variants.add(tail)
        return variants

    # Name-based fallback is fetched conditionally only when UID mismatch is detected.
    dhis2_values_name_norm = {}

    def _element_semantic_signature(el):
        """Semantic signature used to collapse duplicated rows.

        Some datasets return duplicated DE rows with different COC UIDs but the
        same effective combo label (typically default/no-disaggregation fields).
        """
        return (el['deUID'], _norm_coc_name(el['cocName']))

    # De-duplicate semantic duplicates and keep the row most likely to be valid.
    # Preference order:
    # 1) element whose DE|COC key exists in fetched UID values
    # 2) known DHIS2 default COC UID when available
    # 3) first seen
    dedup = {}
    duplicate_rows_removed = 0
    for el in elements:
        sig = _element_semantic_signature(el)
        key = f"{el['deUID']}|{el['cocUID']}"
        in_values = key in dhis2_values_uid

        if sig not in dedup:
            dedup[sig] = el
            continue

        duplicate_rows_removed += 1
        current = dedup[sig]
        current_key = f"{current['deUID']}|{current['cocUID']}"
        current_in_values = current_key in dhis2_values_uid

        if in_values and not current_in_values:
            dedup[sig] = el
            continue

        if not current_in_values and not in_values:
            current_is_default = (current.get('cocUID') == 'HllvX50cXC0')
            candidate_is_default = (el.get('cocUID') == 'HllvX50cXC0')
            if candidate_is_default and not current_is_default:
                dedup[sig] = el

    elements = list(dedup.values())
    elements.sort(
        key=lambda el: (
            int(el.get('sectionOrder', 9999)),
            _norm_name(el.get('sectionName', '')),
            _norm_name(el.get('deName', '')),
            _norm_coc_name(el.get('cocName', '')),
        )
    )

    expected_keys = {
        f"{el['deUID']}|{el['cocUID']}"
        for el in elements
    }
    fetched_uid_keys = set(dhis2_values_uid.keys())
    unmatched_fetched_uid_keys = sorted(fetched_uid_keys - expected_keys)

    if unmatched_fetched_uid_keys:
        # Fallback fetch by names helps when an instance has drifted UID mappings
        # but semantic names still match.
        dhis2_values_name = dhis2.get_data_values(
            org_uid, period, dataset_uid, username, password, id_scheme='name'
        )
        # Normalize name-scheme response keys so matching is case/whitespace robust.
        for raw_key, value in dhis2_values_name.items():
            de_name, coc_name = (raw_key.split('|', 1) + [''])[:2]
            de_norm = _norm_name(de_name)
            for coc_variant in _coc_variants(coc_name):
                norm_key = f"{de_norm}|{coc_variant}"
                dhis2_values_name_norm[norm_key] = value

    # If UID keys from DHIS2 do not match the current dataset matrix, resolve
    # those UIDs to names and attempt a semantic name-based match.
    uid_name_fallback_map = {}
    uid_cocname_fallback_map = {}
    if unmatched_fetched_uid_keys:
        unmatched_de_uids = []
        unmatched_coc_uids = []
        for k in unmatched_fetched_uid_keys:
            de_uid, coc_uid = (k.split('|', 1) + [''])[:2]
            if de_uid:
                unmatched_de_uids.append(de_uid)
            if coc_uid:
                unmatched_coc_uids.append(coc_uid)

        de_name_map = dhis2.get_data_element_name_map(list(set(unmatched_de_uids)), username, password)
        coc_name_map = dhis2.get_coc_name_map(list(set(unmatched_coc_uids)), username, password)

        for k in unmatched_fetched_uid_keys:
            de_uid, coc_uid = (k.split('|', 1) + [''])[:2]
            de_name = de_name_map.get(de_uid, '')
            coc_name = coc_name_map.get(coc_uid, '') if coc_uid else ''
            for coc_variant in _coc_variants(coc_name):
                if de_name:
                    de_norm = _norm_name(de_name)
                    norm_key = f"{de_norm}|{coc_variant}"
                    uid_name_fallback_map[norm_key] = dhis2_values_uid.get(k, '')
                # Extra dynamic path: same DE UID, different DE display name contexts.
                uid_key = f"{de_uid}|{coc_variant}"
                uid_cocname_fallback_map[uid_key] = dhis2_values_uid.get(k, '')

    summary = {k: 0 for k in ('total', 'match', 'differs', 'missing_dhis2', 'missing_local', 'both_empty')}
    rows = []
    matched_by_uid = 0
    matched_by_name_fallback = 0
    matched_by_uid_name_resolution = 0
    matched_by_uid_cocname_resolution = 0
    matched_by_export_file = 0

    for el in elements:
        key_uid = f"{el['deUID']}|{el['cocUID']}"
        de_norm = _norm_name(el['deName'])
        candidate_name_keys = [f"{de_norm}|{v}" for v in _coc_variants(el['cocName'])]
        candidate_uid_combo_keys = [f"{el['deUID']}|{v}" for v in _coc_variants(el['cocName'])]

        if key_uid in dhis2_values_uid:
            dhis2_val = dhis2_values_uid.get(key_uid, '')
            matched_by_uid += 1
        elif any(k in dhis2_values_name_norm for k in candidate_name_keys):
            k = next(k for k in candidate_name_keys if k in dhis2_values_name_norm)
            dhis2_val = dhis2_values_name_norm.get(k, '')
            matched_by_name_fallback += 1
        elif any(k in uid_name_fallback_map for k in candidate_name_keys):
            k = next(k for k in candidate_name_keys if k in uid_name_fallback_map)
            dhis2_val = uid_name_fallback_map.get(k, '')
            matched_by_uid_name_resolution += 1
        elif any(k in uid_cocname_fallback_map for k in candidate_uid_combo_keys):
            k = next(k for k in candidate_uid_combo_keys if k in uid_cocname_fallback_map)
            dhis2_val = uid_cocname_fallback_map.get(k, '')
            matched_by_uid_cocname_resolution += 1
        elif key_uid in dataset_export_map['uid_map']:
            dhis2_val = dataset_export_map['uid_map'].get(key_uid, '')
            matched_by_export_file += 1
        elif f"{de_norm}|{el['cocUID']}" in dataset_export_map['name_cocuid_map']:
            dhis2_val = dataset_export_map['name_cocuid_map'].get(f"{de_norm}|{el['cocUID']}", '')
            matched_by_export_file += 1
        elif any(k in dataset_export_map['name_cocname_map'] for k in candidate_name_keys):
            k = next(k for k in candidate_name_keys if k in dataset_export_map['name_cocname_map'])
            dhis2_val = dataset_export_map['name_cocname_map'].get(k, '')
            matched_by_export_file += 1
        else:
            dhis2_val = ''

        local_val = local_values.get(key_uid, '')

        if dhis2_val == '' and local_val == '':
            status = 'both_empty'
        elif dhis2_val == '':
            status = 'missing_dhis2'
        elif local_val == '':
            status = 'missing_local'
        elif dhis2_val == local_val:
            status = 'match'
        else:
            status = 'differs'

        summary['total'] += 1
        summary[status]  += 1

        rows.append({
            'sectionName': el.get('sectionName', 'Unsectioned') or 'Unsectioned',
            'deName':     el['deName'],
            'deUID':      el['deUID'],
            'cocName':    el['cocName'],
            'cocUID':     el['cocUID'],
            'dhis2Value': dhis2_val,
            'localValue': local_val,
            'status':     status,
        })

    debug = {
        'raw_element_count': len(dedup) + duplicate_rows_removed,
        'deduped_element_count': len(elements),
        'duplicate_element_rows_removed': duplicate_rows_removed,
        'expected_key_count': len(expected_keys),
        'fetched_uid_key_count': len(fetched_uid_keys),
        'fetched_name_key_count': len(dhis2_values_name_norm),
        'unmatched_fetched_uid_key_count': len(unmatched_fetched_uid_keys),
        'unmatched_fetched_uid_key_samples': unmatched_fetched_uid_keys[:10],
        'matched_by_uid': matched_by_uid,
        'matched_by_name_fallback': matched_by_name_fallback,
        'matched_by_uid_name_resolution': matched_by_uid_name_resolution,
        'matched_by_uid_cocname_resolution': matched_by_uid_cocname_resolution,
        'matched_by_export_file': matched_by_export_file,
        'export_map_fallback_enabled': use_export_map_fallback,
        'dataset_export_file_used': dataset_export_map['exists'],
        'dataset_export_row_count': dataset_export_map['row_count'],
    }

    return jsonify({'success': True, 'data': {'rows': rows, 'summary': summary, 'debug': debug}})


def _save_local_values():
    _require_auth_session()
    org_uid     = request.form.get('orgUnitUID', '')
    period      = request.form.get('period', '').strip()
    entries_raw = request.form.get('entries', '[]')

    try:
        entries = json.loads(entries_raw)
    except json.JSONDecodeError:
        return jsonify({'success': False, 'message': 'Malformed entries JSON.'})

    if not isinstance(entries, list) or not entries:
        return jsonify({'success': False, 'message': 'No entries provided.'})

    count = db.save_local_values(org_uid, period, entries)
    return jsonify({'success': True, 'message': f'{count} value(s) saved locally.'})


def _push():
    username, password = _require_auth_session()
    org_uid         = request.form.get('orgUnitUID', '')
    period          = request.form.get('period', '').strip()
    dataset_uid     = request.form.get('dataSetUID', '').strip()
    field_keys_raw  = request.form.get('fieldKeys', '[]')

    try:
        field_keys = json.loads(field_keys_raw)
    except json.JSONDecodeError:
        return jsonify({'success': False, 'message': 'Malformed fieldKeys JSON.'})

    entries = db.get_local_values_for_keys(org_uid, period, field_keys)
    if not entries:
        return jsonify({'success': False, 'message': 'No local values found for the selected fields.'})

    result = dhis2.push_data_values(org_uid, period, dataset_uid, entries, username, password)

    # DHIS2 response shape varies by version/config. Try multiple known paths.
    response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
    imp = (
        result.get('importSummary')
        or response_block.get('importSummary')
        or response_block
        or result
        or {}
    )

    raw_status = (
        imp.get('status')
        or response_block.get('status')
        or result.get('status')
        or result.get('httpStatus')
        or ''
    )

    counts = (
        imp.get('importCount')
        or response_block.get('importCount')
        or result.get('importCount')
        or {}
    )
    imported = int(counts.get('imported', 0) or 0)
    updated  = int(counts.get('updated', 0) or 0)
    ignored  = int(counts.get('ignored', 0) or 0)

    http_status = str(result.get('httpStatus', '') or '').upper()
    http_code = int(result.get('httpStatusCode', 0) or 0)

    # Derive a practical status when DHIS2 omits explicit import status.
    status = str(raw_status).upper() if raw_status else ''
    if not status:
        if imported > 0 or updated > 0:
            status = 'SUCCESS' if ignored == 0 else 'WARNING'
        elif ignored > 0:
            status = 'WARNING'
        elif http_status in ('OK', 'SUCCESS') or (200 <= http_code < 300):
            status = 'OK'
        else:
            status = 'UNKNOWN'

    # Normalize equivalent states.
    if status in ('CREATED', 'ACCEPTED'):
        status = 'SUCCESS'
    if status in ('PARTIAL', 'PARTIAL_SUCCESS'):
        status = 'WARNING'
    if status in ('FAIL', 'FAILED'):
        status = 'ERROR'

    # Optional details from DHIS2 import response.
    desc = (
        imp.get('description')
        or response_block.get('description')
        or result.get('description')
        or ''
    )
    conflict_texts = []
    conflicts = imp.get('conflicts') or response_block.get('conflicts') or []
    if isinstance(conflicts, list):
        for c in conflicts[:3]:
            if isinstance(c, dict):
                value = c.get('value') or c.get('object') or str(c)
            else:
                value = str(c)
            if value:
                conflict_texts.append(value)

    conflict_details = '; '.join(conflict_texts) if conflict_texts else ''
    db.log_sync(
        org_uid, dataset_uid, period, len(entries), imported, updated, ignored, status,
        dhis2_message=desc,
        conflict_details=conflict_details,
    )

    # Outcome classification for UI and logs.
    if status in ('ERROR', 'UNKNOWN'):
        success = False
    elif imported > 0 or updated > 0:
        # Records were applied (possibly with warnings).
        success = True
        if ignored > 0 and status == 'SUCCESS':
            status = 'WARNING'
    elif ignored > 0:
        # Nothing changed and values were ignored.
        success = False
        if status in ('OK', 'SUCCESS'):
            status = 'WARNING'
    else:
        # No explicit counts; treat clean 2xx/OK as success fallback.
        success = status in ('SUCCESS', 'OK')
    msg = f'Status: {status} — Imported: {imported}, Updated: {updated}, Ignored: {ignored}'
    if desc:
        msg += f' — {desc}'
    if conflict_texts:
        msg += ' — Conflicts: ' + '; '.join(conflict_texts)

    # If still unknown, surface raw keys to aid troubleshooting.
    if status == 'UNKNOWN':
        msg += f" — Response keys: {', '.join(sorted(result.keys()))}"
    return jsonify({'success': success, 'message': msg})


def _get_sync_logs():
    _require_auth_session()
    org_uid = request.form.get('orgUnitUID', '')
    logs = db.get_sync_logs(org_uid)
    return jsonify({'success': True, 'data': logs})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True, port=5000, use_reloader=False)
