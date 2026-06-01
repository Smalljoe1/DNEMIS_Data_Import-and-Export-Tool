"""DHIS2 API client — all HTTP calls to DHIS2 are isolated here."""
import time
import requests


DHIS2_BASE = 'https://asc.education.gov.ng/dhis/api'
TIMEOUT_SHORT = 30   # seconds — login, datasets, logs
TIMEOUT_LONG  = 120  # seconds — compare (may fetch large datasets)

def set_base_url(url: str):
    """Allow runtime override of DHIS2 base URL."""
    global DHIS2_BASE
    DHIS2_BASE = url.rstrip('/') + '/api' if not url.endswith('/api') else url

_JSON_HEADERS = {'Accept': 'application/json'}

# Lightweight in-memory metadata cache (per-process).
_CACHE_TTL_SECONDS = 600
_DATASET_ELEMENTS_CACHE = {}
_NAME_CACHE = {
    'dataElements': {},
    'categoryOptionCombos': {},
}


def _cache_get(cache: dict, key):
    entry = cache.get(key)
    if not entry:
        return None
    if entry['expires_at'] < time.time():
        cache.pop(key, None)
        return None
    return entry['value']


def _cache_set(cache: dict, key, value, ttl_seconds=_CACHE_TTL_SECONDS):
    cache[key] = {
        'value': value,
        'expires_at': time.time() + ttl_seconds,
    }


def _get(path: str, username: str, password: str, params: dict = None, timeout: int = TIMEOUT_SHORT) -> dict:
    resp = requests.get(
        f'{DHIS2_BASE}{path}',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _post(path: str, username: str, password: str, payload: dict) -> dict:
    resp = requests.post(
        f'{DHIS2_BASE}{path}',
        auth=(username, password),
        json=payload,
        headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    return resp.json()


def _chunked(values, chunk_size=50):
    """Yield fixed-size chunks from a sequence."""
    for i in range(0, len(values), chunk_size):
        yield values[i:i + chunk_size]


def _get_name_map(resource_path: str, ids: list, username: str, password: str) -> dict:
    """Resolve a list of UIDs to names via metadata endpoint.

    Args:
        resource_path: e.g. '/dataElements' or '/categoryOptionCombos'
    """
    clean_ids = [x for x in ids if x]
    if not clean_ids:
        return {}

    collection_key = resource_path.lstrip('/')
    resource_cache = _NAME_CACHE.get(collection_key, {})

    result = {}
    missing_ids = []
    for uid in clean_ids:
        cached_name = _cache_get(resource_cache, (username, uid))
        if cached_name is None:
            missing_ids.append(uid)
        else:
            result[uid] = cached_name

    for batch in _chunked(missing_ids, chunk_size=50):
        in_clause = '[' + ','.join(batch) + ']'
        data = _get(
            resource_path,
            username,
            password,
            params={
                'fields': 'id,name',
                'paging': 'false',
                'filter': f'id:in:{in_clause}',
            },
        )
        for item in data.get(collection_key, []):
            item_id = item.get('id', '')
            item_name = item.get('name', '')
            result[item_id] = item_name
            _cache_set(resource_cache, (username, item_id), item_name)

    _NAME_CACHE[collection_key] = resource_cache
    return result


def get_data_element_name_map(ids: list, username: str, password: str) -> dict:
    """Return {deUID: deName} for the given data element IDs."""
    return _get_name_map('/dataElements', ids, username, password)


def get_coc_name_map(ids: list, username: str, password: str) -> dict:
    """Return {cocUID: cocName} for the given category option combo IDs."""
    return _get_name_map('/categoryOptionCombos', ids, username, password)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> dict:
    """Authenticate against DHIS2 and return school/user metadata."""
    data = _get(
        '/me',
        username, password,
        params={
            'fields': (
                'id,username,name,'
                'organisationUnits['
                'id,name,code,'
                'parent[id,name,level,parent[id,name,level]]'
                ']'
            )
        },
    )
    org_units = data.get('organisationUnits', [])
    if not org_units:
        raise ValueError('No organisation unit is assigned to this DHIS2 user.')
    ou = org_units[0]
    ward_node = ou.get('parent', {}) or {}
    lga_node = ward_node.get('parent', {}) or {}

    ward_name = ward_node.get('name', '')
    lga_name = ''

    # Prefer explicit level-3 org unit as LGA; fall back to grandparent name.
    if int(ward_node.get('level', 0) or 0) == 3:
        lga_name = ward_node.get('name', '')
    elif int(lga_node.get('level', 0) or 0) == 3:
        lga_name = lga_node.get('name', '')
    else:
        lga_name = lga_node.get('name', '')

    return {
        'orgUnitUID': ou['id'],
        'orgUnitLevel': int(ou.get('level', 0) or 0),
        'schoolName': ou.get('name', ''),
        'schoolCode': ou.get('code', ''),
        'parentName': ward_name,
        'wardName': ward_name,
        'lgaName': lga_name,
        'userName':   data.get('name', username),
    }


def get_descendant_org_units(root_org_unit_uid: str, username: str, password: str,
                             target_level: int | None = None) -> list:
    """Return descendant org units under a root OU.

    When target_level is provided, only that OU level is returned.
    """
    if not root_org_unit_uid:
        return []

    path_filter = f"path:like:/{root_org_unit_uid}"
    data = _get(
        '/organisationUnits',
        username,
        password,
        params={
            'paging': 'false',
            'fields': 'id,name,code,level,parent[id,name,level]',
            'filter': path_filter,
        },
        timeout=TIMEOUT_LONG,
    )
    org_units = data.get('organisationUnits', []) or []
    out = []
    for ou in org_units:
        level = int(ou.get('level', 0) or 0)
        if target_level is not None and level != int(target_level):
            continue
        out.append({
            'id': str(ou.get('id', '') or ''),
            'name': str(ou.get('name', '') or ''),
            'code': str(ou.get('code', '') or ''),
            'level': level,
            'parent': ou.get('parent', {}) if isinstance(ou.get('parent', {}), dict) else {},
        })

    out.sort(key=lambda x: ((x.get('name') or '').lower(), x.get('id') or ''))
    return out


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def get_datasets(username: str, password: str) -> list:
    """Return all datasets visible to the authenticated user."""
    data = _get(
        '/dataSets',
        username, password,
        params={'fields': 'id,name', 'paging': 'false'},
    )
    return data.get('dataSets', [])


def get_dataset_org_units(dataset_uid: str, username: str, password: str) -> list:
    """Return org units explicitly assigned to a dataset.

    Returns a list of dicts: {id, name, code, level, parent}.
    """
    if not dataset_uid:
        return []
    data = _get(
        f'/dataSets/{dataset_uid}',
        username,
        password,
        params={
            'fields': 'id,organisationUnits[id,name,code,level,parent[id,name,level]]',
        },
        timeout=TIMEOUT_LONG,
    )
    out = []
    for ou in data.get('organisationUnits', []) or []:
        out.append({
            'id': str(ou.get('id', '') or ''),
            'name': str(ou.get('name', '') or ''),
            'code': str(ou.get('code', '') or ''),
            'level': int(ou.get('level', 0) or 0),
            'parent': ou.get('parent', {}) if isinstance(ou.get('parent', {}), dict) else {},
        })
    return out


def get_programs(username: str, password: str) -> list:
    """Return programs visible to the authenticated user."""
    data = _get(
        '/programs',
        username,
        password,
        params={
            'fields': 'id,name,programType,programStages[id,name,sortOrder]',
            'paging': 'false',
        },
    )
    programs = data.get('programs', [])
    # Events app flow targets event programs with stages.
    return [p for p in programs if p.get('programStages')]


def get_program_stages(program_uid: str, username: str, password: str) -> list:
    """Return program stages for a program."""
    data = _get(
        f'/programs/{program_uid}',
        username,
        password,
        params={'fields': 'id,name,programStages[id,name,sortOrder]'},
    )
    stages = data.get('programStages', [])
    return sorted(stages, key=lambda s: int(s.get('sortOrder', 9999) or 9999))


def get_program_stage_elements(program_stage_uid: str, username: str, password: str) -> list:
    """Return data elements configured on a program stage."""
    data = _get(
        f'/programStages/{program_stage_uid}',
        username,
        password,
        params={
            'fields': 'id,name,programStageDataElements[dataElement[id,name,valueType]]'
        },
    )
    out = []
    seen = set()
    for psde in data.get('programStageDataElements', []):
        de = psde.get('dataElement', {})
        uid = de.get('id', '')
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append({
            'deUID': uid,
            'deName': de.get('name', uid),
            'deType': de.get('valueType', ''),
        })
    return out


def get_program_attributes(program_uid: str, username: str, password: str) -> list:
    """Return tracked entity attributes configured for a program."""
    data = _get(
        f'/programs/{program_uid}',
        username,
        password,
        params={
            'fields': (
                'id,name,'
                'programTrackedEntityAttributes['
                'displayInList,mandatory,'
                'trackedEntityAttribute[id,name,valueType]'
                ']'
            )
        },
    )
    attrs = []
    seen = set()
    for pta in data.get('programTrackedEntityAttributes', []):
        tea = pta.get('trackedEntityAttribute', {})
        uid = tea.get('id', '')
        if not uid or uid in seen:
            continue
        seen.add(uid)
        attrs.append({
            'attrUID': uid,
            'attrName': tea.get('name', uid),
            'attrType': tea.get('valueType', ''),
            'displayInList': bool(pta.get('displayInList', False)),
            'mandatory': bool(pta.get('mandatory', False)),
        })
    return attrs


def get_program_metadata(program_uid: str, username: str, password: str) -> dict:
    """Return program metadata needed for create flows."""
    data = _get(
        f'/programs/{program_uid}',
        username,
        password,
        params={
            'fields': (
                'id,name,programType,trackedEntityType[id,name],'
                'programTrackedEntityAttributes['
                'displayInList,mandatory,trackedEntityAttribute[id,name,valueType]'
                ']'
            )
        },
    )
    tracked_entity_type = data.get('trackedEntityType', {}) or {}
    return {
        'id': data.get('id', program_uid),
        'name': data.get('name', ''),
        'programType': data.get('programType', ''),
        'trackedEntityType': tracked_entity_type.get('id', '') or tracked_entity_type.get('trackedEntityType', ''),
    }


def _extract_import_reference(result: dict) -> str:
    """Best-effort extraction of created reference UID from DHIS2 import response."""
    if not isinstance(result, dict):
        return ''
    for key in ('reference', 'uid', 'id'):
        if result.get(key):
            return str(result.get(key))

    response = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
    for key in ('reference', 'uid', 'id'):
        if response.get(key):
            return str(response.get(key))

    for key in ('trackedEntityInstance', 'enrollment', 'event'):
        if response.get(key):
            return str(response.get(key))

    import_summary = result.get('importSummary', {}) if isinstance(result.get('importSummary', {}), dict) else {}
    for key in ('reference', 'uid', 'id'):
        if import_summary.get(key):
            return str(import_summary.get(key))

    nested_import_summaries = response.get('importSummaries', []) if isinstance(response.get('importSummaries', []), list) else []
    for item in nested_import_summaries:
        if isinstance(item, dict):
            for key in ('reference', 'uid', 'id', 'trackedEntityInstance', 'enrollment', 'event'):
                if item.get(key):
                    return str(item.get(key))

    nested_import_summary = response.get('importSummary', {}) if isinstance(response.get('importSummary', {}), dict) else {}
    for key in ('reference', 'uid', 'id', 'trackedEntityInstance', 'enrollment', 'event'):
        if nested_import_summary.get(key):
            return str(nested_import_summary.get(key))

    for item in result.get('importSummaries', []) or []:
        if isinstance(item, dict):
            for key in ('reference', 'uid', 'id', 'trackedEntityInstance', 'enrollment', 'event'):
                if item.get(key):
                    return str(item.get(key))
    return ''


def create_tracked_entity_instance(tracked_entity_type_uid: str, org_unit_uid: str,
                                   attributes: list, username: str, password: str) -> dict:
    """Create a tracked entity instance and return the raw DHIS2 response."""
    payload = {
        'trackedEntityType': tracked_entity_type_uid,
        'orgUnit': org_unit_uid,
        'attributes': attributes,
    }
    try:
        return _post('/trackedEntityInstances', username, password, payload)
    except requests.HTTPError as exc:
        response = exc.response
        status_code = response.status_code if response is not None else 'UNKNOWN'
        message = ''
        if response is not None:
            try:
                body = response.json()
                parts = []
                if isinstance(body, dict):
                    if body.get('message'):
                        parts.append(str(body.get('message')))
                    if body.get('description'):
                        parts.append(str(body.get('description')))
                    resp = body.get('response', {}) if isinstance(body.get('response', {}), dict) else {}
                    for key in ('message', 'description'):
                        if resp.get(key):
                            parts.append(str(resp.get(key)))
                    import_conflicts = []
                    if isinstance(resp.get('importSummaries', []), list):
                        for summary in resp.get('importSummaries', []):
                            if not isinstance(summary, dict):
                                continue
                            for conflict in summary.get('conflicts', []) or summary.get('importConflicts', []) or []:
                                if isinstance(conflict, dict):
                                    value = conflict.get('value') or conflict.get('object') or str(conflict)
                                else:
                                    value = str(conflict)
                                import_conflicts.append(value)
                    if import_conflicts:
                        parts.append(' | '.join(import_conflicts[:3]))
                message = '; '.join([p for p in parts if p])
            except ValueError:
                message = (response.text or '').strip()[:300]
        if not message:
            message = str(exc)
        raise ValueError(f"TEI create failed ({status_code}): {message}") from exc


def create_enrollment(program_uid: str, tei_uid: str, org_unit_uid: str,
                      enrollment_date: str, incident_date: str,
                      username: str, password: str) -> dict:
    """Create an enrollment and return the raw DHIS2 response."""
    payload = {
        'program': program_uid,
        'trackedEntityInstance': tei_uid,
        'orgUnit': org_unit_uid,
        'enrollmentDate': enrollment_date,
        'incidentDate': incident_date,
        'status': 'ACTIVE',
    }
    return _post('/enrollments', username, password, payload)


def create_event(program_uid: str, program_stage_uid: str, tei_uid: str,
                 enrollment_uid: str, org_unit_uid: str, event_date: str,
                 data_values: list, username: str, password: str) -> dict:
    """Create an event and return the raw DHIS2 response."""
    payload = {
        'events': [{
            'program': program_uid,
            'programStage': program_stage_uid,
            'trackedEntityInstance': tei_uid,
            'enrollment': enrollment_uid,
            'orgUnit': org_unit_uid,
            'eventDate': event_date,
            'status': 'ACTIVE',
            'dataValues': data_values,
        }]
    }
    try:
        resp = requests.post(
            f'{DHIS2_BASE}/events',
            auth=(username, password),
            json=payload,
            headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
            timeout=TIMEOUT_LONG,
        )
    except requests.exceptions.Timeout:
        # DHIS2 can complete import but respond slowly; retry once with a longer timeout.
        resp = requests.post(
            f'{DHIS2_BASE}/events',
            auth=(username, password),
            json=payload,
            headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
            timeout=180,
        )
    if not resp.ok:
        message = ''
        try:
            body = resp.json()
            parts = []
            if isinstance(body, dict):
                for key in ('message', 'description'):
                    if body.get(key):
                        parts.append(str(body[key]))
                resp_inner = body.get('response', {}) if isinstance(body.get('response', {}), dict) else {}
                for key in ('message', 'description'):
                    if resp_inner.get(key):
                        parts.append(str(resp_inner[key]))
                for summary in (resp_inner.get('importSummaries') or body.get('importSummaries') or []):
                    if not isinstance(summary, dict):
                        continue
                    for conflict in (summary.get('conflicts') or summary.get('importConflicts') or []):
                        val = conflict.get('value') or conflict.get('object') or str(conflict) if isinstance(conflict, dict) else str(conflict)
                        parts.append(val)
            message = '; '.join(p for p in parts if p)
        except ValueError:
            message = (resp.text or '').strip()[:400]
        if not message:
            message = f"HTTP {resp.status_code}"
        raise ValueError(f"Event create failed ({resp.status_code}): {message}")
    return resp.json()


def get_existing_enrollment_for_tei(program_uid: str, tei_uid: str, org_unit_uid: str,
                                    username: str, password: str) -> str:
    """Return an existing enrollment UID for TEI/program/orgUnit, preferring ACTIVE if present."""
    data = _get(
        '/enrollments',
        username,
        password,
        params={
            'program': program_uid,
            'trackedEntityInstance': tei_uid,
            'ou': org_unit_uid,
            'ouMode': 'SELECTED',
            'paging': 'false',
            'fields': 'enrollment,status,enrollmentDate',
        },
        timeout=TIMEOUT_LONG,
    )
    enrollments = data.get('enrollments', [])
    if not enrollments:
        return ''
    active = [e for e in enrollments if str(e.get('status', '')).upper() == 'ACTIVE']
    candidates = active if active else enrollments
    candidates = sorted(candidates, key=lambda e: str(e.get('enrollmentDate', '') or ''), reverse=True)
    return str(candidates[0].get('enrollment', '') or '')


def get_existing_event_for_enrollment(program_stage_uid: str, enrollment_uid: str,
                                      username: str, password: str) -> str:
    """Return an existing event UID for an enrollment/programStage, or empty string if none."""
    data = _get(
        '/events',
        username,
        password,
        params={
            'enrollment': enrollment_uid,
            'programStage': program_stage_uid,
            'paging': 'false',
            'fields': 'event,status,eventDate',
        },
        timeout=TIMEOUT_LONG,
    )
    events = data.get('events', [])
    if not events:
        return ''
    return str(events[0].get('event', '') or '')


def create_tracker_bundle(
    tracked_entity_type_uid: str,
    program_uid: str,
    program_stage_uid: str,
    org_unit_uid: str,
    enrollment_date: str,
    event_date: str,
    attributes: list,
    data_values: list,
    username: str,
    password: str,
    existing_tei_uid: str = '',
) -> dict:
    """Create TEI + enrollment + event in a single atomic call via the new /tracker API.

    If existing_tei_uid is provided the TEI creation step is skipped — only an enrollment
    (under the existing TEI) and a new event are created.

    Returns a dict with keys:
      tei_uid, enrollment_uid, event_uid  — UIDs of the created/found resources (may be '' on failure)
      status  — 'OK' | 'ERROR' | 'WARNING'
      errors  — list of human-readable error strings
    """
    if existing_tei_uid:
        payload = {
            'enrollments': [{
                'trackedEntity': existing_tei_uid,
                'program': program_uid,
                'orgUnit': org_unit_uid,
                'enrollmentDate': enrollment_date,
                'incidentDate': enrollment_date,
                'status': 'ACTIVE',
                'events': [{
                    'program': program_uid,
                    'programStage': program_stage_uid,
                    'orgUnit': org_unit_uid,
                    'trackedEntity': existing_tei_uid,
                    'eventDate': event_date,
                    'status': 'ACTIVE',
                    'dataValues': data_values,
                }],
            }]
        }
    else:
        payload = {
            'trackedEntities': [{
                'trackedEntityType': tracked_entity_type_uid,
                'orgUnit': org_unit_uid,
                'attributes': attributes,
                'enrollments': [{
                    'program': program_uid,
                    'orgUnit': org_unit_uid,
                    'enrollmentDate': enrollment_date,
                    'incidentDate': enrollment_date,
                    'status': 'ACTIVE',
                    'events': [{
                        'program': program_uid,
                        'programStage': program_stage_uid,
                        'orgUnit': org_unit_uid,
                        'eventDate': event_date,
                        'status': 'ACTIVE',
                        'dataValues': data_values,
                    }],
                }],
            }]
        }

    resp = requests.post(
        f'{DHIS2_BASE}/tracker',
        auth=(username, password),
        json=payload,
        headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
        timeout=TIMEOUT_LONG,
    )

    result = {'tei_uid': existing_tei_uid, 'enrollment_uid': '', 'event_uid': '', 'status': 'ERROR', 'errors': []}

    try:
        body = resp.json()
    except ValueError:
        result['errors'].append(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return result

    # New Tracker API response shape
    response = body.get('response', body)
    bundle = response.get('bundleReport', {})
    type_report = bundle.get('typeReportMap', {})

    def _uid_from_report(type_key):
        reports = type_report.get(type_key, {}).get('objectReports', [])
        return str(reports[0].get('uid', '') or '') if reports else ''

    if not existing_tei_uid:
        result['tei_uid'] = _uid_from_report('TRACKED_ENTITY')
    result['enrollment_uid'] = _uid_from_report('ENROLLMENT')
    result['event_uid'] = _uid_from_report('EVENT')

    # Collect validation errors
    validation = response.get('validationReport', {})
    for err in validation.get('errorReports', []):
        msg = err.get('message', '') or str(err)
        result['errors'].append(msg)

    # Derive status
    api_status = str(response.get('status', body.get('status', ''))).upper()
    if api_status in ('OK', 'SUCCESS'):
        result['status'] = 'OK'
    elif api_status == 'WARNING':
        result['status'] = 'WARNING'
    else:
        result['status'] = 'ERROR'

    # If the new API endpoint is not available (404) fall through to old flow
    if resp.status_code == 404:
        result['status'] = 'NOT_FOUND'

    return result


def create_tracker_bundle_bulk(
    tracked_entity_type_uid: str,
    program_uid: str,
    program_stage_uid: str,
    org_unit_uid: str,
    create_groups: list,
    username: str,
    password: str,
) -> dict:
    """Create many rows in one /tracker call.

    Each item in create_groups accepts:
      personId (optional), enrollmentDate, eventDate, attributes, dataValues
    """
    if not create_groups:
        return {'status': 'OK', 'errors': [], 'response': {}, 'submitted': 0}

    tracked_entities = []
    enrollments = []
    for row in create_groups:
        tei_uid = str(row.get('personId', '') or '').strip()
        enrollment_date = str(row.get('enrollmentDate', '') or '').strip()
        event_date = str(row.get('eventDate', '') or '').strip()
        attributes = row.get('attributes', []) or []
        data_values = row.get('dataValues', []) or []

        event_payload = {
            'program': program_uid,
            'programStage': program_stage_uid,
            'orgUnit': org_unit_uid,
            'eventDate': event_date,
            'status': 'ACTIVE',
            'dataValues': data_values,
        }

        if tei_uid:
            enrollments.append({
                'trackedEntity': tei_uid,
                'program': program_uid,
                'orgUnit': org_unit_uid,
                'enrollmentDate': enrollment_date,
                'incidentDate': enrollment_date,
                'status': 'ACTIVE',
                'events': [{**event_payload, 'trackedEntity': tei_uid}],
            })
        else:
            tracked_entities.append({
                'trackedEntityType': tracked_entity_type_uid,
                'orgUnit': org_unit_uid,
                'attributes': attributes,
                'enrollments': [{
                    'program': program_uid,
                    'orgUnit': org_unit_uid,
                    'enrollmentDate': enrollment_date,
                    'incidentDate': enrollment_date,
                    'status': 'ACTIVE',
                    'events': [event_payload],
                }],
            })

    payload = {}
    if tracked_entities:
        payload['trackedEntities'] = tracked_entities
    if enrollments:
        payload['enrollments'] = enrollments

    resp = requests.post(
        f'{DHIS2_BASE}/tracker',
        auth=(username, password),
        json=payload,
        headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
        timeout=TIMEOUT_LONG,
    )

    out = {
        'status': 'ERROR',
        'errors': [],
        'response': {},
        'submitted': len(create_groups),
    }
    if resp.status_code == 404:
        out['status'] = 'NOT_FOUND'
        return out

    try:
        body = resp.json()
    except ValueError:
        out['errors'].append(f"HTTP {resp.status_code}: {(resp.text or '')[:300]}")
        return out

    response = body.get('response', body)
    out['response'] = response
    validation = response.get('validationReport', {}) if isinstance(response, dict) else {}
    for err in validation.get('errorReports', []) or []:
        out['errors'].append(str(err.get('message', '') or err))

    api_status = str(response.get('status', body.get('status', ''))).upper() if isinstance(response, dict) else str(body.get('status', '')).upper()
    if api_status in ('OK', 'SUCCESS'):
        out['status'] = 'OK'
    elif api_status == 'WARNING':
        out['status'] = 'WARNING'
    else:
        out['status'] = 'ERROR'
    return out


def get_tracked_entity_attribute_values(tei_uids: list, username: str, password: str) -> dict:
    """Return mapping: {teiUID: {attributeUID: value}} for TEI attributes."""
    out = {}
    clean = [x for x in tei_uids if x]
    if not clean:
        return out

    def _extract_uid(raw):
        if isinstance(raw, dict):
            return raw.get('id', '') or raw.get('uid', '') or raw.get('attribute', '')
        return str(raw or '')

    def _extract_attrs(inst: dict) -> dict:
        attrs = {}
        raw_attrs = inst.get('attributes', []) or inst.get('trackedEntityAttributeValues', [])
        for item in raw_attrs:
            a_uid = (
                _extract_uid(item.get('attribute', '')) or
                _extract_uid(item.get('trackedEntityAttribute', ''))
            )
            if a_uid:
                attrs[a_uid] = str(item.get('value', '') or '')
        return attrs

    for batch in _chunked(clean, chunk_size=50):
        # Preferred fast path: batch query by TEI IDs.
        try:
            in_clause = '[' + ','.join(batch) + ']'
            data = _get(
                '/trackedEntityInstances',
                username,
                password,
                params={
                    'fields': 'trackedEntityInstance,attributes[attribute,value],trackedEntityAttributeValues[attribute,value]',
                    'skipPaging': 'true',
                    'filter': f'trackedEntityInstance:in:{in_clause}',
                },
            )

            instances = data.get('trackedEntityInstances') or data.get('instances') or []
            for inst in instances:
                tei = _extract_uid(inst.get('trackedEntityInstance', '')) or _extract_uid(inst.get('trackedEntity', ''))
                if tei:
                    out[tei] = _extract_attrs(inst)
            continue
        except requests.HTTPError:
            # Compatibility fallback for DHIS2 instances that do not support this filter.
            pass

        # Fallback: resolve each TEI individually.
        for tei in batch:
            try:
                inst = _get(
                    f'/trackedEntityInstances/{tei}',
                    username,
                    password,
                    params={
                        'fields': 'trackedEntityInstance,attributes[attribute,value],trackedEntityAttributeValues[attribute,value]'
                    },
                )
                tei_id = _extract_uid(inst.get('trackedEntityInstance', tei)) or _extract_uid(inst.get('trackedEntity', '')) or tei
                out[tei_id] = _extract_attrs(inst)
            except requests.HTTPError:
                # Leave missing/unreadable TEIs empty instead of failing the whole events screen.
                out[tei] = {}

    return out


def get_program_enrollment_attribute_values(org_unit_uid: str, program_uid: str,
                                           username: str, password: str) -> dict:
    """Return {teiUID: {attributeUID: value}} from program enrollments.

    Some DHIS2 instances block direct TEI attribute queries for data-entry users.
    Enrollment payloads often still include the same attribute values.
    """
    out = {}

    def _extract_uid(raw):
        if isinstance(raw, dict):
            return raw.get('id', '') or raw.get('uid', '') or raw.get('attribute', '')
        return str(raw or '')

    try:
        data = _get(
            '/enrollments',
            username,
            password,
            params={
                'program': program_uid,
                'ou': org_unit_uid,
                'ouMode': 'SELECTED',
                'paging': 'false',
                'fields': (
                    'trackedEntityInstance,'
                    'attributes[attribute,value],'
                    'trackedEntityAttributeValues[attribute,value]'
                ),
            },
        )
    except requests.HTTPError:
        return out

    enrollments = data.get('enrollments', [])
    for enr in enrollments:
        tei = _extract_uid(enr.get('trackedEntityInstance', '')) or _extract_uid(enr.get('trackedEntity', ''))
        if not tei:
            continue
        attrs = {}
        raw_attrs = enr.get('attributes', []) or enr.get('trackedEntityAttributeValues', [])
        for item in raw_attrs:
            a_uid = (
                _extract_uid(item.get('attribute', '')) or
                _extract_uid(item.get('trackedEntityAttribute', ''))
            )
            if a_uid:
                attrs[a_uid] = str(item.get('value', '') or '')
        if tei not in out:
            out[tei] = {}
        out[tei].update(attrs)

    return out


def get_program_enrollment_attribute_values_by_tei(tei_uids: list, program_uid: str,
                                                   username: str, password: str) -> dict:
    """Return {teiUID: {attributeUID: value}} by querying enrollments per TEI.

    Some instances return empty attributes for bulk enrollment searches but do
    include them when filtered by trackedEntityInstance.
    """
    out = {}
    clean = [x for x in tei_uids if x]
    if not clean:
        return out

    def _extract_uid(raw):
        if isinstance(raw, dict):
            return raw.get('id', '') or raw.get('uid', '') or raw.get('attribute', '')
        return str(raw or '')

    for tei in clean:
        try:
            data = _get(
                '/enrollments',
                username,
                password,
                params={
                    'program': program_uid,
                    'trackedEntityInstance': tei,
                    'paging': 'false',
                    'fields': (
                        'trackedEntityInstance,trackedEntity,'
                        'attributes[attribute,trackedEntityAttribute,value],'
                        'trackedEntityAttributeValues[attribute,trackedEntityAttribute,value]'
                    ),
                },
            )
        except requests.HTTPError:
            continue

        for enr in data.get('enrollments', []):
            tei_id = _extract_uid(enr.get('trackedEntityInstance', '')) or _extract_uid(enr.get('trackedEntity', '')) or tei
            raw_attrs = enr.get('attributes', []) or enr.get('trackedEntityAttributeValues', [])
            attrs = {}
            for item in raw_attrs:
                a_uid = _extract_uid(item.get('attribute', '')) or _extract_uid(item.get('trackedEntityAttribute', ''))
                if a_uid:
                    attrs[a_uid] = str(item.get('value', '') or '')
            if attrs:
                if tei_id not in out:
                    out[tei_id] = {}
                out[tei_id].update(attrs)

    return out


def get_program_tracked_entity_attribute_values(org_unit_uid: str, program_uid: str,
                                                username: str, password: str) -> dict:
    """Return {teiUID: {attributeUID: value}} from trackedEntityInstances by program + org unit.

    Some DNEMIS deployments expose TEI attributes only when listing tracked entities
    in program context rather than fetching TEIs directly.
    """
    out = {}

    def _extract_uid(raw):
        if isinstance(raw, dict):
            return raw.get('id', '') or raw.get('uid', '') or raw.get('attribute', '')
        return str(raw or '')

    def _extract_attrs(inst: dict) -> dict:
        attrs = {}
        raw_attrs = inst.get('attributes', []) or inst.get('trackedEntityAttributeValues', [])
        for item in raw_attrs:
            a_uid = _extract_uid(item.get('attribute', '')) or _extract_uid(item.get('trackedEntityAttribute', ''))
            if a_uid:
                attrs[a_uid] = str(item.get('value', '') or '')
        return attrs

    # Preferred program-scoped TEI list endpoint.
    try:
        data = _get(
            '/trackedEntityInstances',
            username,
            password,
            params={
                'program': program_uid,
                'ou': org_unit_uid,
                'ouMode': 'SELECTED',
                'paging': 'false',
                'fields': (
                    'trackedEntityInstance,trackedEntity,attributes[attribute,trackedEntityAttribute,value],'
                    'trackedEntityAttributeValues[attribute,trackedEntityAttribute,value]'
                ),
            },
        )
        instances = data.get('trackedEntityInstances') or data.get('instances') or []
        for inst in instances:
            tei = _extract_uid(inst.get('trackedEntityInstance', '')) or _extract_uid(inst.get('trackedEntity', ''))
            if tei:
                out[tei] = _extract_attrs(inst)
        if out:
            return out
    except requests.HTTPError:
        pass

    # Legacy fallback: trackedEntityInstances/query returns headers + rows.
    try:
        data = _get(
            '/trackedEntityInstances/query',
            username,
            password,
            params={
                'program': program_uid,
                'ou': org_unit_uid,
                'ouMode': 'SELECTED',
                'paging': 'false',
            },
        )
    except requests.HTTPError:
        return out

    headers = data.get('headers', [])
    rows = data.get('rows', [])
    if not headers or not rows:
        return out

    header_names = [str(h.get('name', '') or h.get('column', '') or '') for h in headers]
    tei_index = None
    for idx, name in enumerate(header_names):
        lowered = name.lower()
        if lowered in ('trackedentityinstance', 'tei', 'instance', 'tracked entity instance'):
            tei_index = idx
            break
    if tei_index is None:
        return out

    attr_indices = {}
    for idx, header in enumerate(headers):
        uid = _extract_uid(header.get('id', '')) or _extract_uid(header.get('uid', ''))
        if uid:
            attr_indices[idx] = uid

    for row in rows:
        if tei_index >= len(row):
            continue
        tei = str(row[tei_index] or '')
        if not tei:
            continue
        attrs = {}
        for idx, attr_uid in attr_indices.items():
            if idx < len(row):
                value = str(row[idx] or '')
                if value:
                    attrs[attr_uid] = value
        if attrs:
            out[tei] = attrs

    return out


def get_events(org_unit_uid: str, program_uid: str, program_stage_uid: str,
               start_date: str, end_date: str, username: str, password: str,
               page_size: int = 250) -> list:
    """Fetch events for org unit + program + stage within a date range using pagination."""
    params_base = {
        'orgUnit': org_unit_uid,
        'ouMode': 'SELECTED',
        'program': program_uid,
        'programStage': program_stage_uid,
        'startDate': start_date,
        'endDate': end_date,
        'fields': 'event,eventDate,completedDate,status,program,programStage,orgUnit,enrollment,trackedEntityInstance,dataValues[dataElement,value]',
    }
    all_events = []
    page = 1
    page_size = max(50, int(page_size or 250))
    while True:
        params = {
            **params_base,
            'page': page,
            'pageSize': page_size,
        }
        resp = requests.get(
            f'{DHIS2_BASE}/events',
            auth=(username, password),
            params=params,
            headers=_JSON_HEADERS,
            timeout=TIMEOUT_LONG,
        )
        resp.raise_for_status()
        body = resp.json()
        batch = body.get('events', [])
        if not batch:
            break
        all_events.extend(batch)
        pager = body.get('pager', {}) if isinstance(body.get('pager', {}), dict) else {}
        page_count = int(pager.get('pageCount', 0) or 0)
        if page_count and page >= page_count:
            break
        if len(batch) < page_size and not page_count:
            break
        page += 1
    return all_events


def get_enrollment_details(enrollment_uids: list, username: str, password: str) -> dict:
    """Return {enrollmentUID: {enrollmentDate, incidentDate, status, program, orgUnit, trackedEntityInstance}}."""
    out = {}

    def _uid(raw):
        if isinstance(raw, dict):
            return str(raw.get('id', '') or raw.get('uid', '') or '')
        return str(raw or '')

    clean_uids = [x for x in enrollment_uids if x]
    resolved = set()

    for batch in _chunked(clean_uids, chunk_size=50):
        in_clause = '[' + ','.join(batch) + ']'
        try:
            data = _get(
                '/enrollments',
                username,
                password,
                params={
                    'paging': 'false',
                    'filter': f'enrollment:in:{in_clause}',
                    'fields': 'enrollment,enrollmentDate,incidentDate,status,program,orgUnit,trackedEntityInstance',
                },
                timeout=TIMEOUT_LONG,
            )
            for item in data.get('enrollments', []) or []:
                enr_id = _uid(item.get('enrollment', ''))
                if not enr_id:
                    continue
                out[enr_id] = {
                    'enrollmentDate': str(item.get('enrollmentDate', '') or ''),
                    'incidentDate': str(item.get('incidentDate', '') or ''),
                    'status': str(item.get('status', '') or ''),
                    'program': _uid(item.get('program', '')),
                    'orgUnit': _uid(item.get('orgUnit', '')),
                    'trackedEntityInstance': _uid(item.get('trackedEntityInstance', '')),
                }
                resolved.add(enr_id)
        except requests.HTTPError:
            # Fall back to per-enrollment retrieval for compatibility.
            pass

        for enr_uid in batch:
            if enr_uid in resolved:
                continue
            try:
                data = _get(
                    f'/enrollments/{enr_uid}',
                    username,
                    password,
                    params={
                        'fields': 'enrollment,enrollmentDate,incidentDate,status,program,orgUnit,trackedEntityInstance',
                    },
                    timeout=TIMEOUT_LONG,
                )
            except requests.HTTPError:
                continue

            enr_id = _uid(data.get('enrollment', '') or enr_uid)
            if enr_id:
                out[enr_id] = {
                    'enrollmentDate': str(data.get('enrollmentDate', '') or ''),
                    'incidentDate': str(data.get('incidentDate', '') or ''),
                    'status': str(data.get('status', '') or ''),
                    'program': _uid(data.get('program', '')),
                    'orgUnit': _uid(data.get('orgUnit', '')),
                    'trackedEntityInstance': _uid(data.get('trackedEntityInstance', '')),
                }
    return out


def push_event_updates(event_updates: list, username: str, password: str) -> dict:
    """Push event updates. event_updates is a list of full event payload objects."""
    if not event_updates:
        return {'status': 'SUCCESS', 'message': '', 'response': {'importCount': {'imported': 0, 'updated': 0, 'ignored': 0}}}

    def _extract_error(resp: requests.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return (resp.text or '').strip()[:300]

        parts = []

        def _collect_conflicts(container):
            if not isinstance(container, dict):
                return
            for conflict in (container.get('conflicts') or container.get('importConflicts') or []):
                if isinstance(conflict, dict):
                    value = conflict.get('value') or conflict.get('object') or conflict.get('message') or str(conflict)
                else:
                    value = str(conflict)
                parts.append(value)

        if isinstance(body, dict):
            for key in ('message', 'description'):
                if body.get(key):
                    parts.append(str(body.get(key)))
            response = body.get('response', {}) if isinstance(body.get('response', {}), dict) else {}
            for key in ('message', 'description'):
                if response.get(key):
                    parts.append(str(response.get(key)))

            import_summary = response.get('importSummary') if isinstance(response.get('importSummary'), dict) else body.get('importSummary')
            if isinstance(import_summary, dict):
                for key in ('message', 'description'):
                    if import_summary.get(key):
                        parts.append(str(import_summary.get(key)))
                _collect_conflicts(import_summary)

            for summary in (response.get('importSummaries') or body.get('importSummaries') or []):
                if not isinstance(summary, dict):
                    continue
                for key in ('message', 'description'):
                    if summary.get(key):
                        parts.append(str(summary.get(key)))
                _collect_conflicts(summary)

            _collect_conflicts(response)
            _collect_conflicts(body)
        return '; '.join(parts)[:300] if parts else (resp.text or '').strip()[:300]

    def _extract_failed_event_ids(body: dict) -> list:
        failed = []
        if not isinstance(body, dict):
            return failed
        response = body.get('response', {}) if isinstance(body.get('response', {}), dict) else {}
        summaries = (response.get('importSummaries') or body.get('importSummaries') or [])
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            imp = summary.get('importCount', {}) if isinstance(summary.get('importCount', {}), dict) else {}
            ignored = int(imp.get('ignored', 0) or 0)
            status = str(summary.get('status', '')).upper()
            if ignored <= 0 and status not in ('ERROR', 'WARNING'):
                continue
            ref = (
                summary.get('reference')
                or summary.get('event')
                or summary.get('uid')
                or summary.get('id')
            )
            if ref:
                failed.append(str(ref))
        return failed

    def _run_put_fallback(target_updates: list) -> dict:
        updated = 0
        ignored = 0
        errors = []

        for item in target_updates:
            ev_uid = str(item.get('event', '') or '')
            if not ev_uid:
                ignored += 1
                errors.append('Missing event UID in update payload')
                continue

            desired_status = str(item.get('status', '') or '').upper()
            has_event_date = bool(str(item.get('eventDate', '') or '').strip())

            def _put_event(payload_item: dict) -> requests.Response:
                return requests.put(
                    f'{DHIS2_BASE}/events/{ev_uid}',
                    auth=(username, password),
                    json=payload_item,
                    headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
                    timeout=TIMEOUT_LONG,
                )

            put_resp = _put_event(item)
            if 200 <= put_resp.status_code < 300:
                updated += 1
                continue

            retried = False
            handled_failure = False
            if put_resp.status_code == 409 and desired_status == 'COMPLETED' and has_event_date:
                # Reopen -> apply full update while active -> re-complete.
                # Use the full item as base for reopen/reclose to avoid value_required_but_not_provided.
                reopen_base = {**item, 'status': 'ACTIVE'}
                reopen_resp = _put_event(reopen_base)
                if 200 <= reopen_resp.status_code < 300:
                    active_payload = {**item, 'status': 'ACTIVE'}
                    active_resp = _put_event(active_payload)
                    if 200 <= active_resp.status_code < 300:
                        reclose_base = {**item, 'status': 'COMPLETED'}
                        reclose_resp = _put_event(reclose_base)
                        if 200 <= reclose_resp.status_code < 300:
                            updated += 1
                            retried = True
                        else:
                            handled_failure = True
                            ignored += 1
                            errors.append(f"{ev_uid}: {_extract_error(reclose_resp)}")
                    else:
                        handled_failure = True
                        ignored += 1
                        errors.append(f"{ev_uid}: {_extract_error(active_resp)}")
                else:
                    handled_failure = True
                    ignored += 1
                    errors.append(f"{ev_uid}: {_extract_error(reopen_resp)}")

            if not retried and not handled_failure:
                ignored += 1
                errors.append(f"{ev_uid}: {_extract_error(put_resp)}")

        status = 'SUCCESS' if ignored == 0 else 'WARNING'
        return {
            'status': status,
            'message': '; '.join(errors[:10]),
            'response': {
                'importCount': {
                    'imported': 0,
                    'updated': updated,
                    'ignored': ignored,
                }
            },
        }

    total_updated = 0
    total_ignored = 0
    all_errors = []
    batch_size = 200

    for batch in _chunked(event_updates, chunk_size=batch_size):
        payload = {'events': batch}
        try:
            resp = requests.post(
                f'{DHIS2_BASE}/events?strategy=UPDATE',
                auth=(username, password),
                json=payload,
                headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
                timeout=TIMEOUT_LONG,
            )
            resp.raise_for_status()
            bulk_result = resp.json()

            response_block = bulk_result.get('response', {}) if isinstance(bulk_result.get('response', {}), dict) else {}
            imp = bulk_result.get('importSummary') or response_block.get('importSummary') or response_block or bulk_result
            import_count = imp.get('importCount', {}) if isinstance(imp, dict) else {}
            ignored = int(import_count.get('ignored', 0) or 0)
            updated = int(import_count.get('updated', 0) or 0)
            imported = int(import_count.get('imported', 0) or 0)
            status = str(imp.get('status', bulk_result.get('status', ''))).upper() if isinstance(imp, dict) else str(bulk_result.get('status', '')).upper()
            batch_updated = updated + imported

            if ignored > 0 or status in ('WARNING', 'ERROR'):
                failed_ids = set(_extract_failed_event_ids(bulk_result))
                if failed_ids:
                    failed_batch = [item for item in batch if str(item.get('event', '') or '') in failed_ids]
                    total_updated += batch_updated
                else:
                    failed_batch = list(batch)
                fallback_result = _run_put_fallback(failed_batch)
                fb_imp = fallback_result.get('response', {}).get('importCount', {}) if isinstance(fallback_result.get('response', {}), dict) else {}
                total_updated += int(fb_imp.get('updated', 0) or 0)
                total_ignored += int(fb_imp.get('ignored', 0) or 0)
                msg = str(fallback_result.get('message', '') or '').strip()
                if msg:
                    all_errors.append(msg)
            else:
                total_updated += batch_updated
        except requests.HTTPError:
            fallback_result = _run_put_fallback(batch)
            fb_imp = fallback_result.get('response', {}).get('importCount', {}) if isinstance(fallback_result.get('response', {}), dict) else {}
            total_updated += int(fb_imp.get('updated', 0) or 0)
            total_ignored += int(fb_imp.get('ignored', 0) or 0)
            msg = str(fallback_result.get('message', '') or '').strip()
            if msg:
                all_errors.append(msg)

    status = 'SUCCESS' if total_ignored == 0 else 'WARNING'
    return {
        'status': status,
        'message': '; '.join(all_errors[:10]),
        'response': {
            'importCount': {
                'imported': 0,
                'updated': total_updated,
                'ignored': total_ignored,
            }
        },
    }


def push_enrollment_updates(enrollment_updates: list, username: str, password: str) -> dict:
    """Push enrollment updates. enrollment_updates is a list of enrollment payload objects."""
    payload = {'enrollments': enrollment_updates}
    try:
        resp = requests.post(
            f'{DHIS2_BASE}/enrollments?strategy=UPDATE',
            auth=(username, password),
            json=payload,
            headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
            timeout=TIMEOUT_LONG,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError:
        # Compatibility fallback for instances that expect per-resource PUT.
        updated = 0
        ignored = 0
        errors = []
        for item in enrollment_updates:
            enr_uid = str(item.get('enrollment', '') or '')
            if not enr_uid:
                ignored += 1
                errors.append('Missing enrollment UID in update payload')
                continue
            put_resp = requests.put(
                f'{DHIS2_BASE}/enrollments/{enr_uid}',
                auth=(username, password),
                json=item,
                headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
                timeout=TIMEOUT_LONG,
            )
            if 200 <= put_resp.status_code < 300:
                updated += 1
            else:
                ignored += 1
                errors.append(f"{enr_uid}: {put_resp.text[:200]}")
        status = 'SUCCESS' if ignored == 0 else 'WARNING'
        return {
            'status': status,
            'message': '; '.join(errors[:3]),
            'response': {
                'importCount': {
                    'imported': 0,
                    'updated': updated,
                    'ignored': ignored,
                }
            },
        }


# ---------------------------------------------------------------------------
# Dataset structure — data elements + category option combos
# ---------------------------------------------------------------------------

def get_dataset_elements(dataset_uid: str, username: str, password: str) -> list:
    """Return [{deUID, deName, cocUID, cocName, sectionName, sectionOrder}] per dataset cell."""
    cache_key = (username, dataset_uid)
    cached = _cache_get(_DATASET_ELEMENTS_CACHE, cache_key)
    if cached is not None:
        # Return a shallow copy to prevent accidental caller mutation of cache.
        return list(cached)

    data = _get(
        f'/dataSets/{dataset_uid}',
        username, password,
        params={
            'fields': (
                'dataSetElements['
                '  categoryCombo[categoryOptionCombos[id,name]],'
                '  dataElement['
                '    id,name,valueType,'
                '    categoryCombo[categoryOptionCombos[id,name]]'
                '  ]'
                '],'
                'sections[id,name,dataElements[id]]'
            )
        },
    )
    section_by_de_uid = {}
    section_order_by_de_uid = {}

    # Resolve section membership via /sections endpoint when possible.
    # This is more reliable for some DHIS2 instances than relying only on
    # embedded dataSet.sections[].dataElements from /dataSets/{id}.
    section_refs = data.get('sections', [])
    section_ids = [s.get('id', '') for s in section_refs if s.get('id', '')]
    section_order_index = {sid: idx for idx, sid in enumerate(section_ids)}

    resolved_sections = []
    if section_ids:
        for batch in _chunked(section_ids, chunk_size=50):
            in_clause = '[' + ','.join(batch) + ']'
            section_data = _get(
                '/sections',
                username,
                password,
                params={
                    'filter': f'id:in:{in_clause}',
                    'fields': 'id,name,dataElements[id,name]',
                    'paging': 'false',
                },
            )
            resolved_sections.extend(section_data.get('sections', []))

    # Keep dataset form order (section sequence) for consistent UI grouping.
    if resolved_sections:
        resolved_sections.sort(key=lambda s: section_order_index.get(s.get('id', ''), 9999))
    else:
        # Fallback to embedded section metadata.
        resolved_sections = section_refs

    for idx, section in enumerate(resolved_sections):
        section_name = section.get('name', '') or 'Unsectioned'
        for de_ref in section.get('dataElements', []):
            de_uid = de_ref.get('id', '')
            if de_uid and de_uid not in section_by_de_uid:
                section_by_de_uid[de_uid] = section_name
                section_order_by_de_uid[de_uid] = idx

    elements = []
    seen = set()
    for dse in data.get('dataSetElements', []):
        de = dse.get('dataElement', {})
        de_uid  = de.get('id', '')
        de_name = de.get('name', '')
        de_type = de.get('valueType', '')
        section_name = section_by_de_uid.get(de_uid, 'Unsectioned')
        section_order = section_order_by_de_uid.get(de_uid, 9999)

        # In DHIS2, a DataSetElement can override the data element's category combo.
        # Prefer the DSE-level combo, then fall back to the DE default combo.
        dse_combo = dse.get('categoryCombo', {})
        de_combo = de.get('categoryCombo', {})
        cocs = dse_combo.get('categoryOptionCombos') or de_combo.get('categoryOptionCombos') or []

        if not cocs:
            key = (de_uid, '')
            if key not in seen:
                seen.add(key)
                elements.append({
                    'deUID': de_uid,
                    'deName': de_name,
                    'deType': de_type,
                    'cocUID': '',
                    'cocName': 'default',
                    'sectionName': section_name,
                    'sectionOrder': section_order,
                })
        else:
            for coc in cocs:
                coc_uid = coc.get('id', '')
                key = (de_uid, coc_uid)
                if key in seen:
                    continue
                seen.add(key)
                elements.append({
                    'deUID':   de_uid,
                    'deName':  de_name,
                    'deType':  de_type,
                    'cocUID':  coc_uid,
                    'cocName': coc.get('name', 'default'),
                    'sectionName': section_name,
                    'sectionOrder': section_order,
                })
    _cache_set(_DATASET_ELEMENTS_CACHE, cache_key, list(elements))
    return elements


# ---------------------------------------------------------------------------
# Data values
# ---------------------------------------------------------------------------

def get_data_values(org_unit_uid: str, period: str, dataset_uid: str,
                    username: str, password: str, id_scheme: str = 'uid') -> dict:
    """Return {dataElement|categoryOptionCombo: value} for DHIS2 values.

    Args:
        id_scheme: 'uid' (default) or 'name'.
            Uses DHIS2 dataElementIdScheme/categoryOptionComboIdScheme for response
            to support robust matching when UID mappings drift across instances.
    """
    scheme = (id_scheme or 'uid').lower()
    if scheme not in ('uid', 'name'):
        raise ValueError("id_scheme must be 'uid' or 'name'.")

    params = {
        'dataSet': dataset_uid,
        'orgUnit': org_unit_uid,
        'period': period,
        'fields': 'dataElement,categoryOptionCombo,value',
        'dataElementIdScheme': scheme,
        'categoryOptionComboIdScheme': scheme,
        'orgUnitIdScheme': 'uid',
    }

    # Use TIMEOUT_LONG for potentially large value sets
    resp = requests.get(
        f'{DHIS2_BASE}/dataValueSets',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    data = resp.json()
    result = {}
    for dv in data.get('dataValues', []):
        de_id = dv.get('dataElement', '')
        coc_id = dv.get('categoryOptionCombo', '')
        value = dv.get('value', '')
        # Use empty string if cocUID is missing, to match elements without combos
        key = f"{de_id}|{coc_id}" if coc_id else f"{de_id}|"
        result[key] = value
    return result


def get_dataset_data_entry_org_units(dataset_uid: str, start_date: str, end_date: str,
                                     parent_org_unit_uid: str,
                                     username: str, password: str,
                                     excluded_data_elements=None) -> set:
    """Return orgUnit UIDs that have at least one data value in the dataset/date range.

    Uses /api/dataValueSets with children=true under the provided parent org unit.
    """
    if not dataset_uid or not parent_org_unit_uid:
        return set()

    excluded = {
        str(x or '').strip()
        for x in (excluded_data_elements or [])
        if str(x or '').strip()
    }

    params = {
        'dataSet': dataset_uid,
        'startDate': start_date,
        'endDate': end_date,
        'orgUnit': parent_org_unit_uid,
        'children': 'true',
        'fields': 'orgUnit,dataElement',
        'orgUnitIdScheme': 'uid',
        'dataElementIdScheme': 'uid',
        'paging': 'false',
    }

    resp = requests.get(
        f'{DHIS2_BASE}/dataValueSets',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    data = resp.json()

    out = set()
    for dv in data.get('dataValues', []) or []:
        de_uid = str(dv.get('dataElement', '') or '').strip()
        if de_uid and de_uid in excluded:
            continue
        raw_ou = dv.get('orgUnit', '')
        if isinstance(raw_ou, dict):
            ou_uid = str(raw_ou.get('id', '') or raw_ou.get('uid', '') or '').strip()
        else:
            ou_uid = str(raw_ou or '').strip()
        if ou_uid:
            out.add(ou_uid)
    return out


def get_completed_dataset_org_units(dataset_uid: str, start_date: str, end_date: str,
                                    parent_org_unit_uid: str,
                                    username: str, password: str) -> set:
    """Return orgUnit UIDs with completed dataset registrations in range.

    Uses /api/completeDataSetRegistrations with children=true under the parent OU.
    """
    if not dataset_uid or not parent_org_unit_uid:
        return set()

    params = {
        'dataSet': dataset_uid,
        'startDate': start_date,
        'endDate': end_date,
        'orgUnit': parent_org_unit_uid,
        'children': 'true',
        'orgUnitIdScheme': 'uid',
        'paging': 'false',
    }

    resp = requests.get(
        f'{DHIS2_BASE}/completeDataSetRegistrations',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    data = resp.json()

    out = set()
    for reg in data.get('completeDataSetRegistrations', []) or []:
        raw_ou = reg.get('organisationUnit')
        if raw_ou is None:
            raw_ou = reg.get('orgUnit', '')
        if isinstance(raw_ou, dict):
            ou_uid = str(raw_ou.get('id', '') or raw_ou.get('uid', '') or '').strip()
        else:
            ou_uid = str(raw_ou or '').strip()
        if ou_uid:
            out.add(ou_uid)
    return out


def push_data_values(org_unit_uid: str, period: str, dataset_uid: str,
                     entries: list, username: str, password: str) -> dict:
    """POST data values to DHIS2. entries: [{deUID, cocUID, value}].
    
    Note: if cocUID is empty string, we omit categoryOptionCombo from the request.
    """
    data_values = []
    for e in entries:
        val = e['value']
        # Exclude if value is nan, None, or empty string
        if val is None:
            continue
        sval = str(val).strip()
        if sval.lower() == 'nan' or sval == '':
            continue
        # If value is a float but represents an integer, send as integer string
        try:
            fval = float(sval)
            if fval.is_integer():
                sval = str(int(fval))
        except Exception:
            pass
        dv = {
            'dataElement': e['deUID'],
            'orgUnit':     org_unit_uid,
            'period':      period,
            'value':       sval,
        }
        # Only include categoryOptionCombo if it's non-empty
        if e.get('cocUID'):
            dv['categoryOptionCombo'] = e['cocUID']
        data_values.append(dv)

    if not data_values:
        return {
            'status': 'OK',
            'message': 'No non-empty values to push.',
            'response': {
                'importCount': {
                    'imported': 0,
                    'updated': 0,
                    'ignored': 0,
                }
            },
        }

    def _result_from_json(raw: dict) -> dict:
        response_block = raw.get('response', {}) if isinstance(raw.get('response', {}), dict) else {}
        imp = raw.get('importSummary') or response_block.get('importSummary') or response_block or raw
        counts = imp.get('importCount', {}) if isinstance(imp, dict) else {}
        imported = int(counts.get('imported', 0) or 0)
        updated = int(counts.get('updated', 0) or 0)
        ignored = int(counts.get('ignored', 0) or 0)
        status = str(
            (imp.get('status', '') if isinstance(imp, dict) else '')
            or raw.get('status', '')
            or raw.get('httpStatus', '')
            or 'UNKNOWN'
        ).upper()
        message = ''
        if isinstance(imp, dict):
            message = str(imp.get('description', '') or imp.get('message', '') or '').strip()
        if not message:
            message = str(raw.get('message', '') or '').strip()
        return {
            'status': status,
            'message': message,
            'imported': imported,
            'updated': updated,
            'ignored': ignored,
            'uncertain': [],
        }

    def _merge_results(parts: list) -> dict:
        imported = sum(int(p.get('imported', 0) or 0) for p in parts)
        updated = sum(int(p.get('updated', 0) or 0) for p in parts)
        ignored = sum(int(p.get('ignored', 0) or 0) for p in parts)
        uncertain = []
        for p in parts:
            uncertain.extend(list(p.get('uncertain', []) or []))
        messages = [str(p.get('message', '') or '').strip() for p in parts if str(p.get('message', '') or '').strip()]
        statuses = {str(p.get('status', '') or '').upper() for p in parts}
        status = 'SUCCESS'
        if ignored > 0 or 'ERROR' in statuses or 'WARNING' in statuses:
            status = 'WARNING'
        elif statuses and statuses.issubset({'OK', 'SUCCESS'}):
            status = 'SUCCESS'
        elif statuses and statuses != {'UNKNOWN'}:
            status = 'OK'
        return {
            'status': status,
            'message': '; '.join(messages[:10]),
            'imported': imported,
            'updated': updated,
            'ignored': ignored,
            'uncertain': uncertain,
        }

    def _canonical_value(value) -> str:
        """Normalize values for robust equality checks after uncertain posts."""
        text = str(value if value is not None else '').strip()
        if text == '':
            return ''
        try:
            num = float(text)
            if num.is_integer():
                return str(int(num))
            return str(num)
        except Exception:
            return text

    def _extract_http_error_message(exc: requests.HTTPError) -> str:
        """Extract the most useful server-side error details from an HTTP error."""
        resp = exc.response
        if resp is None:
            return str(exc)
        try:
            body = resp.json()
            parts = []
            if isinstance(body, dict):
                for key in ('message', 'description', 'httpStatus', 'httpStatusCode'):
                    value = body.get(key)
                    if value:
                        parts.append(str(value))

                response_block = body.get('response', {}) if isinstance(body.get('response', {}), dict) else {}
                for key in ('message', 'description', 'status'):
                    value = response_block.get(key)
                    if value:
                        parts.append(str(value))

                conflicts = response_block.get('conflicts') or body.get('conflicts') or []
                if isinstance(conflicts, list):
                    for conflict in conflicts[:5]:
                        if isinstance(conflict, dict):
                            cval = conflict.get('value') or conflict.get('object') or conflict.get('message') or str(conflict)
                        else:
                            cval = str(conflict)
                        if cval:
                            parts.append(cval)

            clean = [p.strip() for p in parts if str(p).strip()]
            if clean:
                return '; '.join(clean[:8])
        except Exception:
            pass

        text = (resp.text or '').strip()
        if text:
            return text[:500]
        return str(exc)

    small_payload = len(data_values) <= 250

    def _post_batch(batch: list) -> dict:
        payload = {'dataValues': batch}
        request_timeout = 60 if small_payload else TIMEOUT_LONG
        try:
            resp = requests.post(
                f'{DHIS2_BASE}/dataValueSets',
                auth=(username, password),
                json=payload,
                headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
                timeout=request_timeout,
            )
            resp.raise_for_status()
            try:
                body = resp.json()
            except Exception:
                body = {}
            return _result_from_json(body)
        except requests.exceptions.Timeout:
            # For small single-school payloads, fail fast and verify after posting
            # rather than waiting through long retries.
            if small_payload:
                if len(batch) <= 1:
                    return {
                        'status': 'WARNING',
                        'message': 'Timed out posting 1 value. Verifying current DHIS2 state.',
                        'imported': 0,
                        'updated': 0,
                        'ignored': 0,
                        'uncertain': list(batch),
                    }
                mid = max(1, len(batch) // 2)
                return _merge_results([_post_batch(batch[:mid]), _post_batch(batch[mid:])])

            # Retry once with a longer timeout for large payloads; if it still
            # fails, split batch.
            try:
                retry_resp = requests.post(
                    f'{DHIS2_BASE}/dataValueSets',
                    auth=(username, password),
                    json=payload,
                    headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
                    timeout=240,
                )
                retry_resp.raise_for_status()
                try:
                    body = retry_resp.json()
                except Exception:
                    body = {}
                return _result_from_json(body)
            except requests.exceptions.Timeout:
                if len(batch) <= 1:
                    return {
                        'status': 'WARNING',
                        'message': 'Timed out posting 1 value after retry.',
                        'imported': 0,
                        'updated': 0,
                        'ignored': 0,
                        'uncertain': list(batch),
                    }
                mid = max(1, len(batch) // 2)
                return _merge_results([_post_batch(batch[:mid]), _post_batch(batch[mid:])])
        except requests.HTTPError as exc:
            code = int(exc.response.status_code) if exc.response is not None else 0
            if code in (502, 503, 504):
                if len(batch) <= 1:
                    return {
                        'status': 'WARNING',
                        'message': f'DHIS2 gateway error {code} for 1 value.',
                        'imported': 0,
                        'updated': 0,
                        'ignored': 0,
                        'uncertain': list(batch),
                    }
                mid = max(1, len(batch) // 2)
                return _merge_results([_post_batch(batch[:mid]), _post_batch(batch[mid:])])

            detail = _extract_http_error_message(exc)

            # Validation/conflict responses should be surfaced to the UI as warnings,
            # not raised as generic exceptions.
            if code in (400, 409):
                return {
                    'status': 'WARNING',
                    'message': f'DHIS2 rejected {len(batch)} value(s) ({code}): {detail}',
                    'imported': 0,
                    'updated': 0,
                    'ignored': len(batch),
                    'uncertain': [],
                }

            # Authentication/authorization errors should stop immediately with clear detail.
            if code in (401, 403):
                raise ValueError(f'DHIS2 access error ({code}): {detail}')

            raise ValueError(f'DHIS2 post failed ({code}): {detail}')

    parts = []
    for batch in _chunked(data_values, chunk_size=200):
        parts.append(_post_batch(batch))

    merged = _merge_results(parts)

    # If the request timed out or hit gateway issues, DHIS2 may still have processed it.
    uncertain_values = list(merged.get('uncertain', []) or [])
    if uncertain_values:
        try:
            current_values = get_data_values(
                org_unit_uid,
                period,
                dataset_uid,
                username,
                password,
                id_scheme='uid',
            )
            verified = 0
            unresolved = 0
            for dv in uncertain_values:
                de_uid = str(dv.get('dataElement', '') or '')
                coc_uid = str(dv.get('categoryOptionCombo', '') or '')
                key = f"{de_uid}|{coc_uid}" if coc_uid else f"{de_uid}|"
                server_value = current_values.get(key, None)
                if server_value is not None and _canonical_value(server_value) == _canonical_value(dv.get('value', '')):
                    verified += 1
                else:
                    unresolved += 1

            merged['updated'] += verified
            merged['ignored'] += unresolved

            note_parts = []
            if verified:
                note_parts.append(f"Verified {verified} timed-out value(s) as successfully imported on DHIS2.")
            if unresolved:
                note_parts.append(f"Could not verify {unresolved} value(s) after timeout/gateway errors.")
            if note_parts:
                merged['message'] = '; '.join([merged.get('message', ''), ' '.join(note_parts)]).strip('; ').strip()
        except Exception:
            merged['ignored'] += len(uncertain_values)
            fallback_note = (
                f"Could not verify {len(uncertain_values)} timed-out value(s). "
                "Please refresh and compare again."
            )
            merged['message'] = '; '.join([merged.get('message', ''), fallback_note]).strip('; ').strip()

    if merged['ignored'] == 0:
        merged['status'] = 'SUCCESS'
    elif merged['status'] not in ('ERROR',):
        merged['status'] = 'WARNING'

    return {
        'status': merged['status'],
        'message': merged['message'],
        'response': {
            'status': merged['status'],
            'description': merged['message'],
            'importCount': {
                'imported': merged['imported'],
                'updated': merged['updated'],
                'ignored': merged['ignored'],
            }
        },
    }
