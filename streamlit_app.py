# streamlit_app.py
import streamlit as st
import json
import re
import secrets
from datetime import datetime, timezone, timedelta
import requests
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
        'edited_values': {}
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
        
        @st.cache_data(ttl=300)
        def load_datasets():
            try:
                return dhis2.get_datasets(st.session_state.username, st.session_state.password)
            except Exception as e:
                st.error(f"Failed to load datasets: {e}")
                return []
        
        datasets = load_datasets()
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
        
        st.markdown("---")
        if st.button("Logout", type="secondary"):
            logout()
    
    # Main content area
    st.header(f"Data Entry - {st.session_state.school_name}")
    
    if not st.session_state.selected_dataset:
        st.info("Please select a dataset from the sidebar to begin.")
        return
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"**Dataset ID:** `{st.session_state.selected_dataset}`")
    with col2:
        if st.button("Refresh Data", type="primary"):
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
                # Export current local values as CSV
                rows = st.session_state.compare_results['rows']
                df = pd.DataFrame([
                    {
                        'Section': r['sectionName'],
                        'Data Element': r['deName'],
                        'Disaggregation': r['cocName'],
                        'Data Type': r.get('deType', ''),
                        'deUID': r['deUID'],
                        'cocUID': r['cocUID'],
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
                    df = pd.read_csv(uploaded_file)
                    # Validate required columns
                    required_cols = {'deUID', 'cocUID', 'Local Value'}
                    if not required_cols.issubset(df.columns):
                        st.error(f"CSV missing required columns: {required_cols}")
                    else:
                        # Save values to local db
                        entries = [
                            {
                                'deUID': str(row['deUID']),
                                'cocUID': str(row['cocUID']),
                                'value': str(row['Local Value'])
                            }
                            for _, row in df.iterrows()
                        ]
                        count = db.save_local_values(
                            st.session_state.org_unit_uid,
                            str(st.session_state.selected_period),
                            entries
                        )
                        st.success(f"Uploaded and saved {count} values from template.")
                        load_comparison_data()
                except Exception as e:
                    st.error(f"Failed to process uploaded CSV: {e}")
        with col3:
            st.info("Download the template, fill in values, and re-upload to update local data. Then use 'Post All Local Values' to push to DHIS2.")

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
                        if status in ('SUCCESS', 'OK'):
                            st.success(f"✅ Successfully posted! Imported: {imported}, Updated: {updated}")
                            load_comparison_data()
                            st.experimental_rerun()
                        else:
                            st.warning(f"⚠️ Post completed with warnings: {status}")
                            st.info(f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}")
                            st.experimental_rerun()
                    except Exception as e:
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
    """Display the data entry interface with editable fields"""
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

    if st.session_state.compare_results.get('export_map_used'):
        st.info("ℹ️ Using export file fallback for data matching")

    st.markdown("---")

    # Edit mode toggle
    col1, col2 = st.columns([1, 4])
    with col1:
        edit_mode = st.checkbox("Edit Mode", value=st.session_state.edit_mode)
        if edit_mode != st.session_state.edit_mode:
            st.session_state.edit_mode = edit_mode
            if not edit_mode:
                st.session_state.edited_values = {}
            st.rerun()

    # Group by section (only filtered rows)
    sections = {}
    for row in filtered_rows:
        section = row['sectionName']
        if section not in sections:
            sections[section] = []
        sections[section].append(row)

    # Display each section
    for section_name, section_rows in sections.items():
        with st.expander(f"📁 {section_name} ({len(section_rows)} fields)", expanded=True):
            for row in section_rows:
                display_data_row(row)

    # Add push button at the end if in edit mode and there are changes
    if st.session_state.edit_mode and st.session_state.edited_values:
        st.markdown("---")
        if st.button("Push All Changes to DHIS2", type="primary"):
            push_to_dhis2()

def display_data_row(row):
    """Display a single data row with appropriate styling"""
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    
    # Status emoji
    status_icons = {
        'match': '✅',
        'differs': '⚠️',
        'missing_local': '📝',
        'missing_dhis2': '⬆️',
        'both_empty': '⚪'
    }
    status_icon = status_icons.get(row['status'], '❓')
    
    with col1:
        st.markdown(f"**{row['deName']}**")
        if row['cocName'] and row['cocName'] != 'default':
            st.caption(f"Disaggregation: {row['cocName']}")
    
    with col5:
        if row.get('deType'):
            st.caption(f"Type: {row['deType']}")
    
    with col2:
        st.markdown(f"{status_icon} DHIS2: **{row['dhis2Value'] or '—'}**")
    
    with col3:
        if st.session_state.edit_mode:
            current_value = st.session_state.edited_values.get(row['row_key'], row['localValue'])
            new_value = st.text_input(
                "Local Value",
                value=current_value,
                key=f"input_{row['row_key']}",
                label_visibility="collapsed"
            )
            if new_value != current_value:
                st.session_state.edited_values[row['row_key']] = new_value
            st.caption(f"Original: {row['localValue'] or '—'}")
        else:
            if row['localValue']:
                st.markdown(f"📝 Local: **{row['localValue']}**")
            else:
                st.markdown("📝 Local: *empty*")
    
    with col4:
        if st.session_state.edit_mode:
            if st.button("💾 Save", key=f"save_{row['row_key']}"):
                save_single_value(row, st.session_state.edited_values.get(row['row_key'], row['localValue']))
    
    st.markdown("---")

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
    """Display sync history"""
    try:
        logs = db.get_sync_logs(st.session_state.org_unit_uid)
        if logs:
            df = pd.DataFrame(logs)
            df = df.rename(columns={
                'syncedAt': 'Time',
                'dataSetUID': 'Dataset',
                'period': 'Period',
                'imported': 'Imported',
                'updated': 'Updated',
                'ignored': 'Ignored',
                'dhis2Status': 'Status'
            })
            df['Time'] = pd.to_datetime(df['Time']).dt.strftime('%Y-%m-%d %H:%M')
            st.dataframe(df[['Time', 'Status', 'Imported', 'Updated', 'Ignored', 'dhis2Message']], 
                        use_container_width=True)
        else:
            st.info("No sync logs found")
    except Exception as e:
        st.error(f"Failed to load sync logs: {e}")

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
    
    try:
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
            load_comparison_data()
            st.experimental_rerun()
        else:
            st.warning(f"⚠️ Sync completed with warnings: {status}")
            st.info(f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}")
            st.experimental_rerun()
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
        st.experimental_rerun()

# Main execution
init_session_state()

if not check_auth_expiry():
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    login_page()
else:
    main_app()