# streamlit_app.py
import streamlit as st
import json

st.set_page_config(layout="wide")
import re
from datetime import datetime, timezone, timedelta
import pandas as pd

# Import your existing modules
import db
import dhis2
import export_maps

# Initialize database
db.init_db()

# Session state initialization
def init_session_state():
    defaults = {
        'authenticated': False,
        'username': '',
        'password': '',
        'org_unit_uid': '',
        'school_name': '',
        'school_code': '',
        'ward_name': '',
        'lga_name': '',
        'user_name': '',
        'auth_expiry': None,
        'selected_dataset': None,
        'selected_period': '2024',
        'selected_org_unit': None,
        'compare_results': None,
        'edit_mode': False,
        'edited_values': {},
        'show_push_review': False,
        'pending_nav': None,
        'retry_entries': [],
        'section_edit_modes': {}
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def check_auth_expiry():
    if st.session_state.auth_expiry and datetime.now(timezone.utc) > st.session_state.auth_expiry:
        logout()
        return False
    return True

def logout():
    st.session_state.authenticated = False
    st.session_state.username = ''
    st.session_state.password = ''
    st.session_state.auth_expiry = None
    st.rerun()

# Login page
def login_page():
    st.title("DHIS2 Data Import/Export Tool")
    st.markdown("---")
    
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        
        if submitted:
            if not username or not password:
                st.error("Username and password are required.")
                return
            try:
                info = dhis2.login(username, password)
                st.session_state.authenticated = True
                st.session_state.username = username
                st.session_state.password = password
                st.session_state.org_unit_uid = info['orgUnitUID']
                st.session_state.school_name = info['schoolName']
                st.session_state.school_code = info['schoolCode']
                st.session_state.ward_name = info['wardName']
                st.session_state.lga_name = info['lgaName']
                st.session_state.user_name = info['userName']
                st.session_state.auth_expiry = datetime.now(timezone.utc) + timedelta(hours=8)
                # Clear previous dataset selection
                if 'dataset_select' in st.session_state:
                    del st.session_state['dataset_select']
                if 'selected_dataset' in st.session_state:
                    del st.session_state['selected_dataset']
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {str(e)}")

@st.cache_data(ttl=300)
def load_datasets(username, password):
    try:
        return dhis2.get_datasets(username, password)
    except Exception:
        return []

# Main application
def main_app():
    # Sidebar with user info and navigation
    with st.sidebar:
        st.header(f"Welcome, {st.session_state.user_name}")
        st.markdown(f"**School:** {st.session_state.school_name}")
        st.markdown(f"**Code:** {st.session_state.school_code}")
        st.markdown(f"**Ward:** {st.session_state.ward_name}")
        st.markdown(f"**LGA:** {st.session_state.lga_name}")
        st.markdown("---")
        
        # Dataset selection
        st.subheader("Select Dataset")
        
        datasets = load_datasets(st.session_state.username, st.session_state.password)
        dataset_options = {ds['name']: ds['id'] for ds in datasets}

        # Mapping from prefix to dataset name substring and UID
        prefix_map = {
            'IQS': 'A. Adult and Non Formal Education (IQS/IQTE) Census Form-W36yBpVEUkH',
            'JSS': 'A. Junior Secondary School Census Form-uSw8GwPO417',
            'PRY': 'A. Pre-primary and Primary School Census Form-MLTLNUmvS8r',
            'PVT': 'A. Private School Census Form-pJydop5Fpsz',
            'TVET': 'A. Science and Technical Colleges/ Vocational Education Census Form-XERITHzkeSI',
            'SSS': 'A. Senior Secondary School Census Form-RlfDdEEZ317',
        }

        # Try to auto-select dataset based on school name prefix
        default_index = 0
        school_name = st.session_state.school_name or ""
        auto_selected_name = None
        for i, (prefix, dataset_name) in enumerate(prefix_map.items()):
            if school_name.strip().upper().startswith(prefix):
                # Find the dataset name in the options
                for j, name in enumerate(dataset_options.keys()):
                    if dataset_name in name:
                        default_index = j
                        auto_selected_name = name
                        break
                break

        # Set session state for auto-selection if not already set
        if dataset_options:
            if 'dataset_select' not in st.session_state and auto_selected_name:
                st.session_state['dataset_select'] = auto_selected_name
                st.session_state['selected_dataset'] = dataset_options[auto_selected_name]
            selected_dataset_name = st.selectbox(
                "Dataset",
                options=list(dataset_options.keys()),
                key="dataset_select",
                index=default_index
            )
            st.session_state.selected_dataset = dataset_options[selected_dataset_name]
        
        # Period selection
        current_year = datetime.now().year
        years = list(range(current_year - 5, current_year + 1))
        st.session_state.selected_period = st.selectbox(
            "Period (Year)",
            options=years,
            format_func=lambda x: str(x),
            index=len(years)-1
        )

        # Unsaved-change protection: detect dataset or period change while edits pending
        _ds_changed = st.session_state.get('_last_dataset') != st.session_state.selected_dataset
        _per_changed = st.session_state.get('_last_period') != str(st.session_state.selected_period)
        if (_ds_changed or _per_changed) and st.session_state.edited_values:
            st.warning("⚠️ You have unsaved edits. Switching dataset/period will discard them.")
            col_keep, col_discard = st.columns(2)
            with col_keep:
                if st.button("Keep editing", key="nav_keep"):
                    # Revert selectbox choices by restoring last known values
                    st.session_state['dataset_select'] = next(
                        (n for n, i in {ds['name']: ds['id'] for ds in load_datasets(
                            st.session_state.username, st.session_state.password)}.items()
                         if i == st.session_state.get('_last_dataset')), None)
                    st.rerun()
            with col_discard:
                if st.button("Discard & switch", key="nav_discard", type="primary"):
                    st.session_state.edited_values = {}
                    st.session_state.compare_results = None
                    st.session_state['show_push_review'] = False
                    st.session_state['_last_dataset'] = st.session_state.selected_dataset
                    st.session_state['_last_period'] = str(st.session_state.selected_period)
                    st.rerun()
        elif _ds_changed or _per_changed:
            st.session_state.compare_results = None
            st.session_state['show_push_review'] = False
        st.session_state['_last_dataset'] = st.session_state.selected_dataset
        st.session_state['_last_period'] = str(st.session_state.selected_period)

        # Session expiry countdown
        if st.session_state.auth_expiry:
            remaining = st.session_state.auth_expiry - datetime.now(timezone.utc)
            mins = int(remaining.total_seconds() // 60)
            if remaining.total_seconds() <= 0:
                st.error("Session expired — please log in again.")
            elif mins <= 15:
                st.warning(f"⚠️ Session expires in {mins} min")
            else:
                st.caption(f"🔒 Session valid for {mins} min")

        st.markdown("---")
        if st.button("Logout", type="secondary"):
            if st.session_state.edited_values:
                st.session_state['pending_nav'] = 'logout'
            else:
                logout()
    
    # Main content area
    st.header(f"Data Entry - {st.session_state.school_name}")

    # Pending navigation confirmation (logout with unsaved edits)
    if st.session_state.get('pending_nav') == 'logout':
        st.warning("⚠️ You have unsaved edits. Are you sure you want to logout? Your local edits will be lost.")
        col_stay, col_go = st.columns(2)
        with col_stay:
            if st.button("Stay", key="logout_stay"):
                st.session_state['pending_nav'] = None
                st.rerun()
        with col_go:
            if st.button("Logout anyway", key="logout_confirm", type="primary"):
                st.session_state.edited_values = {}
                st.session_state['pending_nav'] = None
                logout()
        return

    if not st.session_state.selected_dataset:
        st.info("Please select a dataset from the sidebar to begin.")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"**Dataset ID:** `{st.session_state.selected_dataset}`")
    with col2:
        if st.button("Refresh Data", type="primary"):
            if st.session_state.edited_values:
                st.warning("⚠️ Refreshing will discard your unsaved edits.")
                if st.button("Discard & Refresh", key="confirm_refresh", type="primary"):
                    st.session_state.edited_values = {}
                    with st.spinner("Loading data from DHIS2..."):
                        load_comparison_data()
            else:
                with st.spinner("Loading data from DHIS2..."):
                    load_comparison_data()
    
    # Load comparison data if needed
    if st.session_state.compare_results is None:
        with st.spinner("Loading data from DHIS2..."):
            load_comparison_data()
    
    # Display data entry interface
    if st.session_state.compare_results:
        display_data_entry_interface()

        st.markdown("---")
        st.subheader("Data Template Export/Import")
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("Download Data Template (CSV)"):
                # Export DHIS2 and local values as CSV. Users should edit Local Value only.
                rows = st.session_state.compare_results['rows']
                df = pd.DataFrame([
                    {
                        'Section': r['sectionName'],
                        'Data Element': r['deName'],
                        'Disaggregation': r['cocName'],
                        'Data Type': r.get('deType', ''),
                        'deUID': r['deUID'],
                        'cocUID': r['cocUID'],
                        'DHIS2 Value': r['dhis2Value'],
                        'Local Value': r['localValue']
                    }
                    for r in rows
                ])
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name=f"data_template_{st.session_state.school_code or 'school'}.csv",
                    mime='text/csv'
                )
        with col2:
            uploaded_file = st.file_uploader("Upload Data Template (CSV)", type=["csv"], key="template_upload")
            if uploaded_file is not None:
                try:
                    df = pd.read_csv(uploaded_file, dtype=str)
                    # Validate required columns
                    required_cols = {'deUID', 'cocUID', 'Local Value'}
                    if not required_cols.issubset(df.columns):
                        st.error(f"CSV missing required columns: {required_cols}")
                    else:
                        # Build type lookup from current compare results
                        type_map = {
                            r['row_key']: r.get('deType', '')
                            for r in (st.session_state.compare_results or {}).get('rows', [])
                        }
                        numeric_types = {
                            'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE',
                            'INTEGER_ZERO_OR_POSITIVE', 'NUMBER', 'UNIT_INTERVAL', 'PERCENTAGE'
                        }
                        bool_types = {'BOOLEAN', 'TRUE_ONLY'}
                        entries, errors = [], []
                        for i, csv_row in df.iterrows():
                            # Import and push must always use Local Value, never DHIS2 Value.
                            raw = str(csv_row.get('Local Value', '')).strip()
                            if raw.lower() in ('nan', ''):
                                continue  # skip blank cells
                            key = f"{str(csv_row['deUID']).strip()}|{str(csv_row['cocUID']).strip()}"
                            de_type = type_map.get(key, '')
                            err = None
                            if de_type in numeric_types:
                                try:
                                    float(raw)
                                except ValueError:
                                    err = f"Expected number, got '{raw}'"
                            elif de_type in bool_types:
                                if raw.lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
                                    err = f"Expected boolean, got '{raw}'"
                            if err:
                                errors.append({
                                    'Row': i + 2,
                                    'deUID': csv_row['deUID'],
                                    'cocUID': csv_row['cocUID'],
                                    'Value': raw,
                                    'Issue': err
                                })
                            else:
                                entries.append({
                                    'deUID': str(csv_row['deUID']).strip(),
                                    'cocUID': str(csv_row['cocUID']).strip(),
                                    'value': raw
                                })
                        if errors:
                            st.warning(f"⚠️ {len(errors)} row(s) failed validation and were skipped:")
                            st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
                        if entries:
                            count = db.save_local_values(
                                st.session_state.org_unit_uid,
                                str(st.session_state.selected_period),
                                entries
                            )
                            st.success(f"Saved {count} value(s). {len(errors)} row(s) skipped.")
                            load_comparison_data()
                        elif not errors:
                            st.info("No values to import (all cells are blank).")
                except Exception as e:
                    st.error(f"Failed to process uploaded CSV: {e}")
        with col3:
            st.info("Template includes 'DHIS2 Value' for guidance. Enter data in 'Local Value' only; upload and sync use Local Value and ignore DHIS2 Value.")

        # Post all local values to DHIS2 (outside edit mode)
        if not st.session_state.edit_mode:
            if st.button("Post All Local Values to DHIS2", type="primary"):
                # Gather all local values and push
                rows = st.session_state.compare_results['rows']
                entries = [
                    {
                        'deUID': r['deUID'],
                        'cocUID': r['cocUID'],
                        'value': r['localValue']
                    }
                    for r in rows if r['localValue'] != ''
                ]
                if not entries:
                    st.warning("No local values to post.")
                else:
                    # Persist local values to DB before pushing
                    db.save_local_values(
                        st.session_state.org_unit_uid,
                        str(st.session_state.selected_period),
                        entries
                    )
                    try:
                        result = dhis2.push_data_values(
                            st.session_state.org_unit_uid,
                            str(st.session_state.selected_period),
                            st.session_state.selected_dataset,
                            entries,
                            st.session_state.username,
                            st.session_state.password
                        )
                        response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
                        imp = result.get('importSummary') or response_block.get('importSummary') or response_block or result
                        imported = imp.get('importCount', {}).get('imported', 0)
                        updated = imp.get('importCount', {}).get('updated', 0)
                        ignored = imp.get('importCount', {}).get('ignored', 0)
                        status = imp.get('status', 'UNKNOWN')
                        message = imp.get('description', '') or imp.get('message', '') or ''
                        conflicts = str(imp.get('conflicts', '')) if imp.get('conflicts') else ''
                        db.log_sync(
                            st.session_state.org_unit_uid,
                            st.session_state.selected_dataset,
                            str(st.session_state.selected_period),
                            len(entries), imported, updated, ignored,
                            status, message, conflicts
                        )
                        if status in ('SUCCESS', 'OK'):
                            st.success(f"✅ Successfully posted! Imported: {imported}, Updated: {updated}")
                            load_comparison_data()
                            st.rerun()
                        else:
                            st.warning(f"⚠️ Post completed with warnings: {status}")
                            st.info(f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}")
                            st.rerun()
                    except Exception as e:
                        db.log_sync(
                            st.session_state.org_unit_uid,
                            st.session_state.selected_dataset,
                            str(st.session_state.selected_period),
                            len(entries), 0, 0, len(entries),
                            'ERROR', str(e), ''
                        )
                        st.error(f"Failed to post to DHIS2: {str(e)}")

    # Sync logs section
    with st.expander("Sync History"):
        display_sync_logs()

def load_comparison_data():
    """Load and compare DHIS2 vs local data"""
    try:
        result = compare_data()
        st.session_state.compare_results = result
        st.session_state.edit_mode = False
        st.session_state.edited_values = {}
    except Exception as e:
        st.error(f"Failed to load comparison data: {str(e)}")
        st.session_state.compare_results = None

def compare_data():
    """Compare DHIS2 data with local data"""
    username = st.session_state.username
    password = st.session_state.password
    org_uid = st.session_state.org_unit_uid
    period = str(st.session_state.selected_period)
    dataset_uid = st.session_state.selected_dataset
    
    # Fetch dataset elements
    elements = dhis2.get_dataset_elements(dataset_uid, username, password)
    
    # Fetch DHIS2 values
    dhis2_values_uid = dhis2.get_data_values(
        org_uid, period, dataset_uid, username, password, id_scheme='uid'
    )
    
    # Fetch local values
    local_values = db.get_local_values(org_uid, period)
    
    # Load export map fallback
    dataset_export_map = export_maps.load_dataset_export_map(dataset_uid)
    
    def _norm_name(val):
        s = (val or '').strip().lower()
        return re.sub(r'\s+', ' ', s)
    
    def _norm_coc_name(val):
        s = _norm_name(val)
        if s in ('', 'default', '(default)'):
            return ''
        return s
    
    def _coc_variants(val):
        base = _norm_coc_name(val)
        variants = {base}
        if ',' in base:
            tail = base.split(',')[-1].strip()
            if tail:
                variants.add(tail)
        return variants
    
    # Name-based fallback
    dhis2_values_name_norm = {}
    
    # De-duplicate elements
    def _element_semantic_signature(el):
        return (el['deUID'], _norm_coc_name(el['cocName']))
    
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
        elif not current_in_values and not in_values:
            current_is_default = (current.get('cocUID') == 'HllvX50cXC0')
            candidate_is_default = (el.get('cocUID') == 'HllvX50cXC0')
            if candidate_is_default and not current_is_default:
                dedup[sig] = el
    
    elements = list(dedup.values())
    
    # Sort elements
    elements.sort(
        key=lambda el: (
            int(el.get('sectionOrder', 9999)),
            _norm_name(el.get('sectionName', '')),
            _norm_name(el.get('deName', '')),
            _norm_coc_name(el.get('cocName', '')),
        )
    )
    
    expected_keys = {f"{el['deUID']}|{el['cocUID']}" for el in elements}
    fetched_uid_keys = set(dhis2_values_uid.keys())
    unmatched_fetched_uid_keys = fetched_uid_keys - expected_keys
    
    if unmatched_fetched_uid_keys:
        dhis2_values_name = dhis2.get_data_values(
            org_uid, period, dataset_uid, username, password, id_scheme='name'
        )
        for raw_key, value in dhis2_values_name.items():
            de_name, coc_name = (raw_key.split('|', 1) + [''])[:2]
            de_norm = _norm_name(de_name)
            for coc_variant in _coc_variants(coc_name):
                norm_key = f"{de_norm}|{coc_variant}"
                dhis2_values_name_norm[norm_key] = value
    
    # Build rows
    rows = []
    for el in elements:
        key_uid = f"{el['deUID']}|{el['cocUID']}"
        de_norm = _norm_name(el['deName'])
        candidate_name_keys = [f"{de_norm}|{v}" for v in _coc_variants(el['cocName'])]
        
        if key_uid in dhis2_values_uid:
            dhis2_val = dhis2_values_uid.get(key_uid, '')
        elif any(k in dhis2_values_name_norm for k in candidate_name_keys):
            k = next(k for k in candidate_name_keys if k in dhis2_values_name_norm)
            dhis2_val = dhis2_values_name_norm.get(k, '')
        elif key_uid in dataset_export_map['uid_map']:
            dhis2_val = dataset_export_map['uid_map'].get(key_uid, '')
        else:
            dhis2_val = ''
        
        local_val = local_values.get(key_uid, '')
        
        # Determine status
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
        
        rows.append({
            'sectionName': el.get('sectionName', 'Unsectioned') or 'Unsectioned',
            'deName': el['deName'],
            'deUID': el['deUID'],
            'deType': el.get('deType', ''),
            'cocName': el['cocName'],
            'cocUID': el['cocUID'],
            'dhis2Value': dhis2_val,
            'localValue': local_val,
            'status': status,
            'row_key': key_uid
        })
    
    return {
        'rows': rows,
        'duplicate_rows_removed': duplicate_rows_removed,
        'export_map_used': dataset_export_map['exists']
    }

def display_data_entry_interface():
    """Display the data entry interface with table-based editable fields"""
    rows = st.session_state.compare_results['rows']

    # Data element search box
    search_term = st.text_input("Search data elements or disaggregations...", value=st.session_state.get('search_term', ''), key="search_box")
    st.session_state['search_term'] = search_term
    if search_term:
        search_lower = search_term.lower()
        filtered_rows = [
            r for r in rows
            if search_lower in r['deName'].lower() or search_lower in (r['cocName'] or '').lower()
        ]
    else:
        filtered_rows = rows

    # Status filter
    _status_filter = st.radio(
        "Show",
        options=['All', 'Needs Attention', 'Differs', 'Missing Local', 'Missing DHIS2', 'Matched'],
        horizontal=True,
        key='status_filter'
    )
    _filter_map = {
        'Needs Attention': {'differs', 'missing_local', 'missing_dhis2'},
        'Differs': {'differs'},
        'Missing Local': {'missing_local'},
        'Missing DHIS2': {'missing_dhis2'},
        'Matched': {'match'},
    }
    if _status_filter != 'All':
        filtered_rows = [r for r in filtered_rows if r['status'] in _filter_map[_status_filter]]

    # Summary statistics
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    total = len(filtered_rows)
    matches = sum(1 for r in filtered_rows if r['status'] == 'match')
    differs = sum(1 for r in filtered_rows if r['status'] == 'differs')
    missing_local = sum(1 for r in filtered_rows if r['status'] == 'missing_local')
    missing_dhis2 = sum(1 for r in filtered_rows if r['status'] == 'missing_dhis2')

    with col1:
        st.metric("Total Fields", total)
    with col2:
        st.metric("Matched", matches, delta="✓")
    with col3:
        st.metric("Differs", differs, delta="⚠️" if differs > 0 else None)
    with col4:
        st.metric("Missing Local", missing_local, delta="📝")
    with col5:
        st.metric("Missing DHIS2", missing_dhis2, delta="⬆️")

    cr = st.session_state.compare_results
    diag_parts = []
    if cr.get('export_map_used'):
        diag_parts.append("export file fallback active")
    if cr.get('duplicate_rows_removed', 0) > 0:
        diag_parts.append(f"{cr['duplicate_rows_removed']} duplicate row(s) removed from dataset metadata")
    if diag_parts:
        st.info("ℹ️ " + " · ".join(diag_parts))

    st.markdown("---")

    # Pre-push review replaces the table when active
    if st.session_state.get('show_push_review'):
        display_push_review()
        return

    # Retry banner for ignored rows from last push
    if st.session_state.get('retry_entries'):
        retry_list = st.session_state['retry_entries']
        st.warning(f"⚠️ {len(retry_list)} row(s) were ignored by DHIS2 in the last push and need attention.")
        if st.button("🔄 Load ignored rows into Edit Mode", key="load_retry"):
            row_lookup = {r['row_key']: r for r in rows}
            for entry in retry_list:
                key = f"{entry['deUID']}|{entry['cocUID']}"
                if key in row_lookup:
                    st.session_state.edited_values[key] = entry['value']
            st.session_state['_force_all_sections_edit'] = True
            st.session_state['retry_entries'] = []
            st.rerun()
        st.markdown("---")

    # Group by section
    sections = {}
    for row in filtered_rows:
        section = row['sectionName']
        if section not in sections:
            sections[section] = []
        sections[section].append(row)

    # Activate edit mode for all sections (triggered by retry banner)
    if st.session_state.get('_force_all_sections_edit'):
        for sn in sections:
            st.session_state['section_edit_modes'][sn] = True
        del st.session_state['_force_all_sections_edit']

    status_icons = {
        'match': '✅', 'differs': '⚠️', 'missing_local': '📝',
        'missing_dhis2': '⬆️', 'both_empty': '⚪'
    }

    for section_name, section_rows in sections.items():
        safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', section_name)
        with st.expander(f"📁 {section_name} ({len(section_rows)} fields)", expanded=True):
            _sec_editing = st.session_state['section_edit_modes'].get(section_name, False)
            _spacer, _edit_col = st.columns([8, 1])
            with _edit_col:
                _sec_toggle = st.checkbox("✏️ Edit", value=_sec_editing, key=f"sec_edit_{safe_key}")
            if _sec_toggle != _sec_editing:
                st.session_state['section_edit_modes'][section_name] = _sec_toggle
                if not _sec_toggle:
                    st.session_state['show_push_review'] = False
                st.rerun()
            if _sec_editing:
                df_edit = pd.DataFrame([
                    {
                        'Data Element': r['deName'],
                        'Disaggregation': r['cocName'] or 'Default',
                        'Type': r.get('deType', ''),
                        'DHIS2 Value': r['dhis2Value'] or '',
                        'Local Value': st.session_state.edited_values.get(r['row_key'], r['localValue'] or ''),
                    }
                    for r in section_rows
                ])
                edited_df = st.data_editor(
                    df_edit,
                    key=f"editor_{safe_key}",
                    disabled=['Data Element', 'Disaggregation', 'Type', 'DHIS2 Value'],
                    use_container_width=True,
                    hide_index=True,
                    num_rows="fixed",
                )
                # Track changes from the table back to edited_values
                for row, new_val in zip(section_rows, edited_df['Local Value']):
                    new_str = str(new_val).strip() if new_val is not None and str(new_val).strip().lower() != 'nan' else ''
                    if new_str != (row['localValue'] or ''):
                        st.session_state.edited_values[row['row_key']] = new_str
                    elif row['row_key'] in st.session_state.edited_values and new_str == (row['localValue'] or ''):
                        del st.session_state.edited_values[row['row_key']]
            else:
                df_view = pd.DataFrame([
                    {
                        'Status': status_icons.get(r['status'], '❓'),
                        'Data Element': r['deName'],
                        'Disaggregation': r['cocName'] or 'Default',
                        'Type': r.get('deType', ''),
                        'DHIS2 Value': r['dhis2Value'] or '—',
                        'Local Value': r['localValue'] or '—',
                    }
                    for r in section_rows
                ])
                st.dataframe(df_view, use_container_width=True, hide_index=True)

    # Show review-and-push button when there are pending edits
    if any(st.session_state.get('section_edit_modes', {}).values()):
        changed_count = sum(
            1 for r in rows
            if r['row_key'] in st.session_state.edited_values
            and st.session_state.edited_values[r['row_key']] != (r['localValue'] or '')
        )
        if changed_count > 0:
            st.markdown("---")
            if st.button(f"Review & Push {changed_count} Change(s) to DHIS2", type="primary"):
                st.session_state['show_push_review'] = True
                st.rerun()


def display_push_review():
    """Show a pre-push diff table and ask for confirmation before sending to DHIS2"""
    st.subheader("📋 Review Changes Before Pushing to DHIS2")
    st.markdown("---")

    rows = st.session_state.compare_results['rows']
    changed = []
    for row in rows:
        new_val = st.session_state.edited_values.get(row['row_key'])
        if new_val is not None and new_val != (row['localValue'] or ''):
            changed.append({
                'Section': row['sectionName'],
                'Data Element': row['deName'],
                'Disaggregation': row['cocName'] or 'Default',
                'Type': row.get('deType', ''),
                'Current DHIS2': row['dhis2Value'] or '—',
                'Old Local': row['localValue'] or '—',
                'New Value': new_val,
            })

    if not changed:
        st.info("No changes detected to push.")
        if st.button("← Go Back"):
            st.session_state['show_push_review'] = False
            st.rerun()
        return

    st.info(f"**{len(changed)} field(s)** will be updated in DHIS2:")
    st.dataframe(pd.DataFrame(changed), use_container_width=True, hide_index=True)
    st.markdown("---")

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("✅ Confirm & Push", type="primary"):
            st.session_state['show_push_review'] = False
            st.session_state['_do_push'] = True
    with col2:
        if st.button("← Cancel, Go Back", type="secondary"):
            st.session_state['show_push_review'] = False
            st.rerun()

    if st.session_state.pop('_do_push', False):
        push_to_dhis2()

def save_single_value(row, new_value):
    """Save a single edited value"""
    try:
        entries = [{
            'deUID': row['deUID'],
            'cocUID': row['cocUID'],
            'value': new_value
        }]
        count = db.save_local_values(
            st.session_state.org_unit_uid,
            str(st.session_state.selected_period),
            entries
        )
        if count > 0:
            st.success(f"Saved: {row['deName']} = {new_value}")
            # Reload data
            load_comparison_data()
        else:
            st.error("Failed to save value")
    except Exception as e:
        st.error(f"Error saving: {str(e)}")

def display_sync_logs():
    """Display sync audit dashboard and history with conflict inspection"""
    try:
        logs = db.get_sync_logs(st.session_state.org_unit_uid)
        if not logs:
            st.info("No sync logs found")
            return

        df_all = pd.DataFrame(logs)

        # ── Audit dashboard ──────────────────────────────────────────────
        total_syncs = len(df_all)
        success_mask = df_all['dhis2Status'].isin(['SUCCESS', 'OK'])
        error_mask = df_all['dhis2Status'] == 'ERROR'
        success_count = success_mask.sum()
        error_count = error_mask.sum()
        failure_rate = round(100 * error_count / total_syncs, 1) if total_syncs else 0

        last_ok = df_all.loc[success_mask, 'syncedAt'].max() if success_count else None
        last_err = df_all.loc[error_mask, 'syncedAt'].max() if error_count else None

        def _fmt_ts(ts):
            if ts is None:
                return '—'
            try:
                return pd.to_datetime(ts).strftime('%Y-%m-%d %H:%M')
            except Exception:
                return str(ts)

        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Total Syncs", total_syncs)
        mc2.metric("Successful", success_count)
        mc3.metric("Errors", error_count, delta=f"{failure_rate}% fail rate" if error_count else None,
                   delta_color="inverse")
        mc4.metric("Last Success", _fmt_ts(last_ok))
        mc5.metric("Last Error", _fmt_ts(last_err))

        # Top error messages
        if error_count:
            top_errors = (
                df_all.loc[error_mask, 'dhis2Message']
                .value_counts()
                .head(3)
            )
            with st.expander("Top error messages"):
                for msg, cnt in top_errors.items():
                    st.markdown(f"- **{cnt}×** `{msg or '(no message)'}`")

        st.markdown("---")

        # ── Log table ────────────────────────────────────────────────────
        df_all['Time'] = pd.to_datetime(df_all['syncedAt']).dt.strftime('%Y-%m-%d %H:%M')
        display_cols = ['Time', 'dhis2Status', 'batchSize', 'imported', 'updated', 'ignored', 'dhis2Message']
        df_display = df_all[display_cols].rename(columns={
            'dhis2Status': 'Status',
            'batchSize': 'Batch',
            'imported': 'Imported',
            'updated': 'Updated',
            'ignored': 'Ignored',
            'dhis2Message': 'Message',
        })
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        # ── Per-row conflict inspection ───────────────────────────────────
        conflict_rows = [r for r in logs if r.get('conflictDetails') and r['conflictDetails'] not in ('', '[]', 'None', None)]
        if conflict_rows:
            with st.expander(f"🔍 Conflict details ({len(conflict_rows)} sync(s) with conflicts)"):
                for r in conflict_rows[:10]:
                    ts = _fmt_ts(r.get('syncedAt'))
                    st.markdown(f"**{ts}** — status: `{r.get('dhis2Status')}`")
                    raw = r.get('conflictDetails', '')
                    try:
                        parsed = json.loads(raw) if raw.startswith('[') else raw
                        if isinstance(parsed, list):
                            for c in parsed:
                                obj = c if isinstance(c, str) else str(c)
                                st.caption(f"• {obj}")
                        else:
                            st.caption(raw)
                    except Exception:
                        st.caption(raw)
                    st.markdown("---")

    except Exception as e:
        st.error(f"Failed to load sync logs: {e}")


_POSITIVE_INT_TYPES = {'INTEGER_POSITIVE', 'INTEGER_ZERO_OR_POSITIVE'}
_NUMERIC_TYPES = {'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE',
                  'INTEGER_ZERO_OR_POSITIVE', 'NUMBER', 'UNIT_INTERVAL', 'PERCENTAGE'}
_BOOL_TYPES = {'BOOLEAN', 'TRUE_ONLY'}


def _validate_push_entries(entries, rows):
    """
    Run data-quality checks on entries before pushing.
    Returns (clean_entries, issues) where issues is a list of dicts.
    """
    row_meta = {r['row_key']: r for r in rows}
    clean, issues = [], []

    for entry in entries:
        key = f"{entry['deUID']}|{entry['cocUID']}"
        meta = row_meta.get(key, {})
        de_type = meta.get('deType', '')
        name = meta.get('deName', key)
        val = str(entry['value']).strip()
        issue = None

        if val == '':
            issue = "Empty value will be skipped by DHIS2"

        elif de_type in _NUMERIC_TYPES:
            try:
                num = float(val)
                if de_type == 'INTEGER_POSITIVE' and num <= 0:
                    issue = f"Must be > 0 (got {val})"
                elif de_type == 'INTEGER_ZERO_OR_POSITIVE' and num < 0:
                    issue = f"Must be ≥ 0 (got {val})"
                elif de_type == 'INTEGER_NEGATIVE' and num >= 0:
                    issue = f"Must be < 0 (got {val})"
                elif de_type == 'UNIT_INTERVAL' and not (0 <= num <= 1):
                    issue = f"Must be between 0 and 1 (got {val})"
                elif de_type == 'PERCENTAGE' and not (0 <= num <= 100):
                    issue = f"Must be between 0 and 100 (got {val})"
                elif de_type in ('INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE',
                                 'INTEGER_ZERO_OR_POSITIVE') and num != int(num):
                    issue = f"Must be a whole number (got {val})"
            except ValueError:
                issue = f"Expected a number (got '{val}')"

        elif de_type in _BOOL_TYPES:
            if val.lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
                issue = f"Expected true/false (got '{val}')"

        if issue:
            issues.append({'Data Element': name, 'Value': val, 'Type': de_type, 'Issue': issue})
        else:
            clean.append(entry)

    return clean, issues


def push_to_dhis2():
    """Push edited values to DHIS2"""
    if not st.session_state.edited_values:
        st.warning("No changes to push")
        return
    
    # Build entries list
    entries = []
    for row in st.session_state.compare_results['rows']:
        if row['row_key'] in st.session_state.edited_values:
            new_value = st.session_state.edited_values[row['row_key']]
            if new_value != row['localValue']:
                entries.append({
                    'deUID': row['deUID'],
                    'cocUID': row['cocUID'],
                    'value': new_value
                })
    
    if not entries:
        st.warning("No changes detected")
        return

    # Data quality validation before push
    rows = st.session_state.compare_results['rows']
    entries, dq_issues = _validate_push_entries(entries, rows)
    if dq_issues:
        st.warning(f"⚠️ {len(dq_issues)} value(s) failed quality checks and were removed from this push:")
        st.dataframe(pd.DataFrame(dq_issues), use_container_width=True, hide_index=True)
    if not entries:
        st.error("All values failed validation — nothing was sent to DHIS2.")
        return

    try:
        db.save_local_values(
            st.session_state.org_unit_uid,
            str(st.session_state.selected_period),
            entries
        )
        sent_entry_map = {f"{e['deUID']}|{e['cocUID']}": e for e in entries}
        result = dhis2.push_data_values(
            st.session_state.org_unit_uid,
            str(st.session_state.selected_period),
            st.session_state.selected_dataset,
            entries,
            st.session_state.username,
            st.session_state.password
        )
        # Parse response
        response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
        imp = result.get('importSummary') or response_block.get('importSummary') or response_block or result
        imported = imp.get('importCount', {}).get('imported', 0)
        updated = imp.get('importCount', {}).get('updated', 0)
        ignored = imp.get('importCount', {}).get('ignored', 0)
        status = imp.get('status', 'UNKNOWN')
        message = imp.get('description', '') or imp.get('message', '') or ''
        conflicts = ''
        if 'conflicts' in imp:
            conflicts = str(imp['conflicts'])
        db.log_sync(
            st.session_state.org_unit_uid,
            st.session_state.selected_dataset,
            str(st.session_state.selected_period),
            len(entries),
            imported,
            updated,
            ignored,
            status,
            message,
            conflicts
        )
        if status in ('SUCCESS', 'OK'):
            st.success(f"✅ Successfully synced! Imported: {imported}, Updated: {updated}")
            st.session_state.edited_values = {}
            st.session_state['section_edit_modes'] = {}
            st.session_state['retry_entries'] = []
        else:
            # Identify ignored entries for retry
            retry = []
            if ignored > 0:
                conflict_uids = set()
                if conflicts:
                    try:
                        raw_list = json.loads(conflicts) if conflicts.strip().startswith('[') else []
                        for c in raw_list:
                            obj = c.get('object', '') if isinstance(c, dict) else str(c)
                            for part in obj.split():
                                if len(part) == 11:  # DHIS2 UIDs are 11 chars
                                    conflict_uids.add(part)
                    except Exception:
                        pass
                if conflict_uids:
                    retry = [e for e in entries
                             if e['deUID'] in conflict_uids or e['cocUID'] in conflict_uids]
                else:
                    retry = list(entries)
            st.session_state['retry_entries'] = retry
            retry_keys = {f"{e['deUID']}|{e['cocUID']}" for e in retry}
            # Keep only values that were not accepted so the review button does not retain synced edits.
            st.session_state.edited_values = {
                k: v['value'] for k, v in sent_entry_map.items() if k in retry_keys
            }
            if not st.session_state.edited_values:
                st.session_state['section_edit_modes'] = {}
            st.warning(f"⚠️ Sync completed with warnings: {status}")
            st.info(f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}")
    except Exception as e:
        db.log_sync(
            st.session_state.org_unit_uid,
            st.session_state.selected_dataset,
            str(st.session_state.selected_period),
            len(entries),
            0, 0, len(entries),
            'ERROR',
            str(e),
            ''
        )
        st.error(f"Failed to push to DHIS2: {str(e)}")

# Main execution
init_session_state()

if not check_auth_expiry():
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    login_page()
else:
    main_app()