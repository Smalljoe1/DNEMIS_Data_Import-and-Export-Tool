# streamlit_app.py
import streamlit as st
import json
from concurrent.futures import ThreadPoolExecutor

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
        'instance_url': '',
        'org_unit_uid': '',
        'root_org_unit_uid': '',
        'root_org_unit_level': 0,
        'school_name': '',
        'school_code': '',
        'ward_name': '',
        'lga_name': '',
        'user_name': '',
        'auth_expiry': None,
        'selected_dataset': None,
        'selected_period': '2024',
        'selected_org_unit': None,
        'selected_org_units': [],
        'selected_org_unit_labels': {},
        'compare_results': None,
        'edit_mode': False,
        'edited_values': {},
        'show_push_review': False,
        'pending_nav': None,
        'retry_entries': [],
        'section_edit_modes': {},
        'data_mode': 'Aggregate Forms',
        'selected_program': None,
        'selected_program_stage': None,
        'event_rows': [],
        'event_element_specs': [],
        'event_attr_specs': [],
        'event_original_values': {},
        'event_meta': {},
        'event_program_meta': {},
        'event_attr_columns': [],
        'event_attr_stats': {},
        'event_template_overrides': {},
        'event_import_create_changes': [],
        'event_last_uploaded_template_sig': '',
        'event_show_review': False,
        'event_has_unsaved_edits': False,
        'events_editor_rev': 0,
        'aggregate_post_feedback': None,
        'aggregate_import_feedback': None,
        'dhis2_cache_buster': 0
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
        instance_url = st.text_input(
            "DNEMIS Instance URL",
            value=st.session_state.get('instance_url', ''),
            help="Enter the base URL of your DNEMIS/DHIS2 instance (e.g., https://your-instance.com/dhis)"
        )
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        
        if submitted:
            if not username or not password or not instance_url:
                st.error("Instance URL, username, and password are required.")
                return
            try:
                # Set the DHIS2 base URL before login
                dhis2.set_base_url(instance_url)
                st.session_state.instance_url = instance_url
                info = dhis2.login(username, password)
                st.session_state.authenticated = True
                st.session_state.username = username
                st.session_state.password = password
                st.session_state.org_unit_uid = info['orgUnitUID']
                st.session_state.root_org_unit_uid = info['orgUnitUID']
                st.session_state.root_org_unit_level = int(info.get('orgUnitLevel', 0) or 0)
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
def load_datasets(instance_url, username, password):
    try:
        dhis2.set_base_url(instance_url)
        return dhis2.get_datasets(username, password)
    except Exception:
        return []


@st.cache_data(ttl=300)
def load_programs(instance_url, username, password):
    try:
        dhis2.set_base_url(instance_url)
        return dhis2.get_programs(username, password)
    except Exception:
        return []


@st.cache_data(ttl=300)
def load_program_stages(instance_url, program_uid, username, password):
    if not program_uid:
        return []
    try:
        dhis2.set_base_url(instance_url)
        return dhis2.get_program_stages(program_uid, username, password)
    except Exception:
        return []


@st.cache_data(ttl=300)
def load_descendant_level5_org_units(instance_url, root_org_uid, username, password):
    if not root_org_uid:
        return []
    try:
        dhis2.set_base_url(instance_url)
        return dhis2.get_descendant_org_units(
            root_org_uid,
            username,
            password,
            target_level=5,
        )
    except Exception:
        return []


@st.cache_data(ttl=300)
def load_dataset_org_units(instance_url, dataset_uid, username, password):
    if not dataset_uid:
        return []
    try:
        dhis2.set_base_url(instance_url)
        return dhis2.get_dataset_org_units(dataset_uid, username, password)
    except Exception:
        return []


@st.cache_data(ttl=120)
def load_data_values_cached(instance_url, org_unit_uid, period, dataset_uid, username, password, id_scheme='uid', cache_buster=0):
    """Cached wrapper around DHIS2 dataValueSets fetch for aggregate compare."""
    _ = cache_buster
    dhis2.set_base_url(instance_url)
    return dhis2.get_data_values(
        org_unit_uid,
        period,
        dataset_uid,
        username,
        password,
        id_scheme=id_scheme,
    )

# Main application
def main_app():
    instance_url = str(st.session_state.get('instance_url', '') or '')
    if instance_url:
        dhis2.set_base_url(instance_url)

    # Sidebar with user info and navigation
    with st.sidebar:
        st.header(f"Welcome, {st.session_state.user_name}")
        if st.session_state.get('instance_url'):
            st.caption(f"Active Instance: {instance_url}")
        st.markdown(f"**Org Unit:** {st.session_state.school_name}")
        st.markdown(f"**Code:** {st.session_state.school_code}")
        st.markdown(f"**Ward:** {st.session_state.ward_name}")
        st.markdown(f"**LGA:** {st.session_state.lga_name}")
        st.markdown("---")
        
        # Data type selection
        st.subheader("Data Type")
        st.radio(
            "Choose data model",
            options=["Aggregate Forms", "Events"],
            key="data_mode"
        )

        # Period selection (shared)
        current_year = datetime.now().year
        years = list(range(current_year - 5, current_year + 1))
        st.session_state.selected_period = st.selectbox(
            "Period (Year)",
            options=years,
            format_func=lambda x: str(x),
            index=len(years)-1
        )

        if st.session_state.data_mode == "Aggregate Forms":
            st.subheader("Select Dataset")

            datasets = load_datasets(instance_url, st.session_state.username, st.session_state.password)
            dataset_options = {ds['name']: ds['id'] for ds in datasets}

            # Mapping from school type to dataset name substring
            prefix_map = {
                'IQS': 'A. Adult and Non Formal Education (IQS/IQTE) Census Form-W36yBpVEUkH',
                'JSS': 'A. Junior Secondary School Census Form-uSw8GwPO417',
                'PRY': 'A. Pre-primary and Primary School Census Form-MLTLNUmvS8r',
                'PVT': 'A. Private School Census Form-pJydop5Fpsz',
                'TVET': 'A. Science and Technical Colleges/ Vocational Education Census Form-XERITHzkeSI',
                'SSS': 'A. Senior Secondary School Census Form-RlfDdEEZ317',
            }

            default_index = 0
            auto_selected_name = None
            school_name = (st.session_state.school_name or "").upper()
            school_code = (st.session_state.school_code or "").upper()

            for school_type, dataset_name in prefix_map.items():
                name_tokens = school_name.split()
                code_tokens = school_code.split()

                if (school_name.startswith(school_type) or
                    school_type in school_code or
                    school_type in name_tokens or
                    school_type in code_tokens):
                    for j, name in enumerate(dataset_options.keys()):
                        if dataset_name in name:
                            default_index = j
                            auto_selected_name = name
                            break
                    break

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

                prev_dataset_uid = str(st.session_state.get('_last_dataset', '') or '')
                curr_dataset_uid = str(st.session_state.selected_dataset or '')
                if curr_dataset_uid and prev_dataset_uid and curr_dataset_uid != prev_dataset_uid:
                    try:
                        level5_ous = load_descendant_level5_org_units(
                            instance_url,
                            st.session_state.get('root_org_unit_uid', ''),
                            st.session_state.username,
                            st.session_state.password,
                        )
                        level5_ids = {
                            str(ou.get('id', '') or '')
                            for ou in level5_ous
                            if str(ou.get('id', '') or '')
                        }
                        dataset_ous = load_dataset_org_units(
                            instance_url,
                            curr_dataset_uid,
                            st.session_state.username,
                            st.session_state.password,
                        )
                        dataset_level5_ids = {
                            str(ou.get('id', '') or '')
                            for ou in dataset_ous
                            if str(ou.get('id', '') or '') in level5_ids
                        }
                        if dataset_level5_ids:
                            overlap = [
                                uid for uid in st.session_state.get('selected_org_units', [])
                                if uid in dataset_level5_ids
                            ]
                            if not overlap:
                                st.session_state.selected_org_units = []
                                st.session_state.selected_org_unit_labels = {}
                                st.session_state['org_units_multi_select'] = []
                                st.info("Dataset changed and no previously selected schools match it. School selection was cleared.")
                    except Exception:
                        pass

            # Org unit scope selection (after dataset): Level 3 users can target Level 5 schools under their LGA.
            if int(st.session_state.get('root_org_unit_level', 0) or 0) < 5:
                level5_ous = load_descendant_level5_org_units(
                    instance_url,
                    st.session_state.get('root_org_unit_uid', ''),
                    st.session_state.username,
                    st.session_state.password,
                )
                if level5_ous:
                    st.subheader("Data Org Unit")
                    filtered_level5_ous = list(level5_ous)
                    selected_dataset_uid = str(st.session_state.get('selected_dataset', '') or '')
                    dataset_filter_applied = False
                    if selected_dataset_uid:
                        dataset_org_units = load_dataset_org_units(
                            instance_url,
                            selected_dataset_uid,
                            st.session_state.username,
                            st.session_state.password,
                        )
                        dataset_ou_ids = {
                            str(ou.get('id', '') or '')
                            for ou in dataset_org_units
                            if str(ou.get('id', '') or '')
                        }
                        if dataset_ou_ids:
                            candidate = [
                                ou for ou in level5_ous
                                if str(ou.get('id', '') or '') in dataset_ou_ids
                            ]
                            if candidate:
                                filtered_level5_ous = candidate
                                dataset_filter_applied = True

                    filtered_ou_options = {}
                    filtered_ou_label_by_uid = {}
                    for ou in filtered_level5_ous:
                        uid = str(ou.get('id', '') or '')
                        name = str(ou.get('name', '') or '')
                        code = str(ou.get('code', '') or '')
                        label = f"{name} ({code})" if code else name
                        filtered_ou_options[label] = ou
                        filtered_ou_label_by_uid[uid] = label
                    filtered_labels = list(filtered_ou_options.keys())

                    default_uids = [uid for uid in st.session_state.get('selected_org_units', []) if uid in filtered_ou_label_by_uid]
                    default_labels = [filtered_ou_label_by_uid[uid] for uid in default_uids if uid in filtered_ou_label_by_uid]

                    selected_labels = st.multiselect(
                        "Level 5 schools (same dataset)",
                        options=filtered_labels,
                        default=default_labels,
                        key="org_units_multi_select",
                    )
                    selected_uids = [str(filtered_ou_options[lbl].get('id', '') or '') for lbl in selected_labels]
                    st.session_state.selected_org_units = selected_uids
                    st.session_state.selected_org_unit_labels = {
                        str(filtered_ou_options[lbl].get('id', '') or ''): str(filtered_ou_options[lbl].get('name', '') or '')
                        for lbl in selected_labels
                    }
                    if selected_uids:
                        primary_uid = selected_uids[0]
                        primary_ou = next((x for x in filtered_level5_ous if str(x.get('id', '') or '') == primary_uid), None)
                        st.session_state.org_unit_uid = primary_uid
                        if primary_ou:
                            st.session_state.school_name = str(primary_ou.get('name', '') or st.session_state.school_name)
                            st.session_state.school_code = str(primary_ou.get('code', '') or '')
                    if dataset_filter_applied:
                        st.caption("Aggregate school list is filtered to schools assigned to the selected dataset.")
                    else:
                        st.caption("Aggregate fetch/export/import/push will use all selected schools.")

            # Unsaved-change protection: detect dataset or period change while edits pending
            _ds_changed = st.session_state.get('_last_dataset') != st.session_state.selected_dataset
            _per_changed = st.session_state.get('_last_period') != str(st.session_state.selected_period)
            _ou_changed = st.session_state.get('_last_org_units') != list(st.session_state.get('selected_org_units', []))
            if (_ds_changed or _per_changed or _ou_changed) and st.session_state.edited_values:
                st.warning("ÔÜá´©Å You have unsaved edits. Switching dataset/period/org units will discard them.")
                col_keep, col_discard = st.columns(2)
                with col_keep:
                    if st.button("Keep editing", key="nav_keep"):
                        st.session_state['dataset_select'] = next(
                            (n for n, i in {ds['name']: ds['id'] for ds in load_datasets(
                                instance_url, st.session_state.username, st.session_state.password)}.items()
                             if i == st.session_state.get('_last_dataset')), None)
                        st.rerun()
                with col_discard:
                    if st.button("Discard & switch", key="nav_discard", type="primary"):
                        st.session_state.edited_values = {}
                        st.session_state.compare_results = None
                        st.session_state['show_push_review'] = False
                        st.session_state['_last_dataset'] = st.session_state.selected_dataset
                        st.session_state['_last_period'] = str(st.session_state.selected_period)
                        st.session_state['_last_org_units'] = list(st.session_state.get('selected_org_units', []))
                        st.rerun()
            elif _ds_changed or _per_changed or _ou_changed:
                st.session_state.compare_results = None
                st.session_state['show_push_review'] = False
            st.session_state['_last_dataset'] = st.session_state.selected_dataset
            st.session_state['_last_period'] = str(st.session_state.selected_period)
            st.session_state['_last_org_units'] = list(st.session_state.get('selected_org_units', []))

        else:
            if int(st.session_state.get('root_org_unit_level', 0) or 0) < 5:
                level5_ous = load_descendant_level5_org_units(
                    instance_url,
                    st.session_state.get('root_org_unit_uid', ''),
                    st.session_state.username,
                    st.session_state.password,
                )
                if level5_ous:
                    st.subheader("Data Org Unit")
                    ou_options = {}
                    ou_label_by_uid = {}
                    for ou in level5_ous:
                        uid = str(ou.get('id', '') or '')
                        name = str(ou.get('name', '') or '')
                        code = str(ou.get('code', '') or '')
                        label = f"{name} ({code})" if code else name
                        ou_options[label] = ou
                        ou_label_by_uid[uid] = label

                    labels = list(ou_options.keys())
                    default_uids = [uid for uid in st.session_state.get('selected_org_units', []) if uid in ou_label_by_uid]
                    if not default_uids:
                        current_uid = str(st.session_state.get('org_unit_uid', '') or '')
                        default_uids = [current_uid] if current_uid in ou_label_by_uid else []
                    default_labels = [ou_label_by_uid[uid] for uid in default_uids if uid in ou_label_by_uid]

                    selected_labels = st.multiselect(
                        "Level 5 schools",
                        options=labels,
                        default=default_labels,
                        key="org_units_multi_select_events",
                    )
                    selected_uids = [str(ou_options[lbl].get('id', '') or '') for lbl in selected_labels]
                    st.session_state.selected_org_units = selected_uids
                    st.session_state.selected_org_unit_labels = {
                        str(ou_options[lbl].get('id', '') or ''): str(ou_options[lbl].get('name', '') or '')
                        for lbl in selected_labels
                    }
                    if selected_uids:
                        primary_uid = selected_uids[0]
                        primary_ou = next((x for x in level5_ous if str(x.get('id', '') or '') == primary_uid), None)
                        st.session_state.org_unit_uid = primary_uid
                        if primary_ou:
                            st.session_state.school_name = str(primary_ou.get('name', '') or st.session_state.school_name)
                            st.session_state.school_code = str(primary_ou.get('code', '') or '')
                    st.caption("Events fetch/export/import/push will use all selected schools.")

            st.subheader("Select Program Stage")
            programs = load_programs(instance_url, st.session_state.username, st.session_state.password)
            program_options = {p['name']: p['id'] for p in programs}

            if not program_options:
                st.warning("No event programs available for this account.")
            else:
                selected_program_name = st.selectbox(
                    "Program",
                    options=list(program_options.keys()),
                    key="program_select"
                )
                st.session_state.selected_program = program_options[selected_program_name]

                stages = load_program_stages(
                    instance_url,
                    st.session_state.selected_program,
                    st.session_state.username,
                    st.session_state.password
                )
                stage_options = {s['name']: s['id'] for s in stages}
                if stage_options:
                    selected_stage_name = st.selectbox(
                        "Program Stage",
                        options=list(stage_options.keys()),
                        key="program_stage_select"
                    )
                    st.session_state.selected_program_stage = stage_options[selected_stage_name]

            _prog_changed = st.session_state.get('_last_program') != st.session_state.selected_program
            _stage_changed = st.session_state.get('_last_program_stage') != st.session_state.selected_program_stage
            _per_changed = st.session_state.get('_last_period_events') != str(st.session_state.selected_period)
            _ou_changed = st.session_state.get('_last_org_units_events') != list(st.session_state.get('selected_org_units', []))

            if (_prog_changed or _stage_changed or _per_changed or _ou_changed) and st.session_state.get('event_has_unsaved_edits'):
                st.warning("ÔÜá´©Å You have unsaved event edits. Switching context will discard them.")
                keep_col, discard_col = st.columns(2)
                with keep_col:
                    if st.button("Keep editing", key="events_nav_keep"):
                        prev_program = st.session_state.get('_last_program')
                        if prev_program:
                            st.session_state['program_select'] = next(
                                (n for n, i in program_options.items() if i == prev_program),
                                st.session_state.get('program_select')
                            )
                            prev_stages = load_program_stages(
                                instance_url,
                                prev_program,
                                st.session_state.username,
                                st.session_state.password
                            )
                            prev_stage_map = {s['name']: s['id'] for s in prev_stages}
                            prev_stage = st.session_state.get('_last_program_stage')
                            if prev_stage:
                                st.session_state['program_stage_select'] = next(
                                    (n for n, i in prev_stage_map.items() if i == prev_stage),
                                    st.session_state.get('program_stage_select')
                                )
                        st.rerun()
                with discard_col:
                    if st.button("Discard & switch", key="events_nav_discard", type="primary"):
                        st.session_state['event_show_review'] = False
                        st.session_state['event_has_unsaved_edits'] = False
                        st.session_state['events_editor_rev'] += 1
                        st.session_state['_last_program'] = st.session_state.selected_program
                        st.session_state['_last_program_stage'] = st.session_state.selected_program_stage
                        st.session_state['_last_period_events'] = str(st.session_state.selected_period)
                        st.session_state['_last_org_units_events'] = list(st.session_state.get('selected_org_units', []))
                        load_events_data()
                        st.rerun()
            elif _prog_changed or _stage_changed or _per_changed or _ou_changed:
                st.session_state['event_show_review'] = False
                st.session_state['event_has_unsaved_edits'] = False
                st.session_state['_last_program'] = st.session_state.selected_program
                st.session_state['_last_program_stage'] = st.session_state.selected_program_stage
                st.session_state['_last_period_events'] = str(st.session_state.selected_period)
                st.session_state['_last_org_units_events'] = list(st.session_state.get('selected_org_units', []))
                if st.session_state.selected_program and st.session_state.selected_program_stage:
                    load_events_data()

        # Session expiry countdown
        if st.session_state.auth_expiry:
            remaining = st.session_state.auth_expiry - datetime.now(timezone.utc)
            mins = int(remaining.total_seconds() // 60)
            if remaining.total_seconds() <= 0:
                st.error("Session expired ÔÇö please log in again.")
            elif mins <= 15:
                st.warning(f"ÔÜá´©Å Session expires in {mins} min")
            else:
                st.caption(f"­ƒöÆ Session valid for {mins} min")

        st.markdown("---")
        if st.button("Logout", type="secondary"):
            if st.session_state.edited_values or st.session_state.get('event_has_unsaved_edits'):
                st.session_state['pending_nav'] = 'logout'
            else:
                logout()
    
    # Main content area
    selected_ous = list(st.session_state.get('selected_org_units', []) or [])
    if st.session_state.data_mode == "Aggregate Forms" and len(selected_ous) > 1:
        st.header(f"Data Entry - {len(selected_ous)} Selected Schools")
    else:
        st.header(f"Data Entry - {st.session_state.school_name}")
    if st.session_state.get('instance_url'):
        st.info(f"Active DHIS2 Instance: {instance_url}")

    # Pending navigation confirmation (logout with unsaved edits)
    if st.session_state.get('pending_nav') == 'logout':
        st.warning("ÔÜá´©Å You have unsaved edits. Are you sure you want to logout? Your local edits will be lost.")
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

    if st.session_state.data_mode == "Events":
        display_events_interface()
        with st.expander("Sync History"):
            display_sync_logs()
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
                st.warning("ÔÜá´©Å Refreshing will discard your unsaved edits.")
                if st.button("Discard & Refresh", key="confirm_refresh", type="primary"):
                    st.session_state.edited_values = {}
                    st.session_state['dhis2_cache_buster'] = int(st.session_state.get('dhis2_cache_buster', 0) or 0) + 1
                    with st.spinner("Loading data from DHIS2..."):
                        load_comparison_data()
            else:
                st.session_state['dhis2_cache_buster'] = int(st.session_state.get('dhis2_cache_buster', 0) or 0) + 1
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

        import_feedback = st.session_state.get('aggregate_import_feedback')
        if isinstance(import_feedback, dict):
            fb_level = str(import_feedback.get('level', 'info') or 'info').lower()
            fb_title = str(import_feedback.get('title', '') or '').strip()
            fb_detail = str(import_feedback.get('detail', '') or '').strip()
            if fb_level == 'success':
                st.success(fb_title or "Last import completed successfully.")
            elif fb_level == 'warning':
                st.warning(fb_title or "Last import completed with warnings.")
            elif fb_level == 'error':
                st.error(fb_title or "Last import failed.")
            else:
                st.info(fb_title or "Last import result available.")
            if fb_detail:
                st.caption(fb_detail)
            if st.button("Dismiss last import message", key="dismiss_aggregate_import_feedback"):
                st.session_state['aggregate_import_feedback'] = None
                st.rerun()

        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("Download Data Template (CSV)"):
                # Export DHIS2 and local values as CSV. Users should edit Local Value only.
                rows = st.session_state.compare_results['rows']
                df = pd.DataFrame([
                    {
                        'orgUnitUID': r.get('orgUnitUID', st.session_state.org_unit_uid),
                        'Org Unit': r.get('orgUnitName', st.session_state.school_name),
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
                            row_org_uid = str(csv_row.get('orgUnitUID', '') or '').strip() or st.session_state.org_unit_uid
                            key = f"{row_org_uid}|{str(csv_row['deUID']).strip()}|{str(csv_row['cocUID']).strip()}"
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
                            elif de_type == 'DATE':
                                normalized_date, parse_issue = _normalize_aggregate_date(raw)
                                if parse_issue:
                                    err = parse_issue
                                else:
                                    raw = normalized_date
                            elif de_type == 'DATETIME':
                                normalized_datetime, parse_issue = _normalize_aggregate_datetime(raw)
                                if parse_issue:
                                    err = parse_issue
                                else:
                                    raw = normalized_datetime
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
                                    'orgUnitUID': row_org_uid,
                                    'deUID': str(csv_row['deUID']).strip(),
                                    'cocUID': str(csv_row['cocUID']).strip(),
                                    'value': raw
                                })
                        if errors:
                            st.warning(f"ÔÜá´©Å {len(errors)} row(s) failed validation and were skipped:")
                            st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
                        if entries:
                            by_ou = {}
                            for e in entries:
                                by_ou.setdefault(str(e.get('orgUnitUID', '') or st.session_state.org_unit_uid), []).append({
                                    'deUID': e['deUID'],
                                    'cocUID': e['cocUID'],
                                    'value': e['value'],
                                })

                            total_chunks = sum(
                                max(1, (len(ou_entries) + _CSV_SAVE_CHUNK_SIZE - 1) // _CSV_SAVE_CHUNK_SIZE)
                                for ou_entries in by_ou.values()
                            )
                            chunk_index = 0
                            save_progress = st.progress(0, text="Preparing import save chunks...")
                            count = 0
                            for ou_uid, ou_entries in by_ou.items():
                                for chunk in _iter_chunks(ou_entries, _CSV_SAVE_CHUNK_SIZE):
                                    count += db.save_local_values(
                                        ou_uid,
                                        str(st.session_state.selected_period),
                                        chunk
                                    )
                                    chunk_index += 1
                                    save_progress.progress(
                                        chunk_index / max(total_chunks, 1),
                                        text=f"Saved import chunk {chunk_index} of {total_chunks}"
                                    )
                            save_progress.empty()
                            st.session_state['aggregate_import_feedback'] = {
                                'level': 'success',
                                'title': f"Saved {count} value(s).",
                                'detail': f"{len(errors)} row(s) skipped during import.",
                            }
                            st.success(f"Saved {count} value(s). {len(errors)} row(s) skipped.")
                            load_comparison_data()
                        elif not errors:
                            st.session_state['aggregate_import_feedback'] = {
                                'level': 'info',
                                'title': "No values to import.",
                                'detail': "All Local Value cells were blank.",
                            }
                            st.info("No values to import (all cells are blank).")
                except Exception as e:
                    st.error(f"Failed to process uploaded CSV: {e}")
        with col3:
            st.info("Template includes 'DHIS2 Value' for guidance. Enter data in 'Local Value' only; upload and sync use Local Value and ignore DHIS2 Value.")

        # Post all local values to DHIS2 (outside edit mode)
        if not st.session_state.edit_mode:
            if st.button("Post All Local Values to DHIS2", type="primary"):
                st.session_state['aggregate_post_feedback'] = None
                # Gather all local values and push
                rows = st.session_state.compare_results['rows']
                entries = [
                    {
                        'orgUnitUID': r.get('orgUnitUID', st.session_state.org_unit_uid),
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
                        by_ou = {}
                        for e in entries:
                            ou_uid = str(e.get('orgUnitUID', '') or st.session_state.org_unit_uid)
                            by_ou.setdefault(ou_uid, []).append({
                                'deUID': e['deUID'],
                                'cocUID': e['cocUID'],
                                'value': e['value'],
                            })

                        progress = st.progress(0, text="Preparing to post local values to DHIS2...")
                        status_placeholder = st.empty()

                        imported = updated = ignored = 0
                        status = 'SUCCESS'
                        message_parts = []
                        total_ous = max(len(by_ou), 1)
                        total_chunks = sum(
                            max(1, (len(ou_entries) + _AGGREGATE_PUSH_CHUNK_SIZE - 1) // _AGGREGATE_PUSH_CHUNK_SIZE)
                            for ou_entries in by_ou.values()
                        )
                        done_chunks = 0

                        with st.spinner("Posting local values to DHIS2..."):
                            for index, (ou_uid, ou_entries) in enumerate(by_ou.items(), start=1):
                                db.save_local_values(
                                    ou_uid,
                                    str(st.session_state.selected_period),
                                    ou_entries
                                )
                                ou_imported = ou_updated = ou_ignored = 0
                                ou_status = 'SUCCESS'
                                ou_message_parts = []

                                chunks = list(_iter_chunks(ou_entries, _AGGREGATE_PUSH_CHUNK_SIZE))
                                for school_chunk_index, ou_chunk in enumerate(chunks, start=1):
                                    in_flight_chunk = done_chunks + 1
                                    progress.progress(
                                        done_chunks / max(total_chunks, 1),
                                        text=(
                                            f"Sending chunk {in_flight_chunk} of {total_chunks} "
                                            f"(school {index}/{total_ous}, {len(ou_chunk)} values)..."
                                        ),
                                    )
                                    status_placeholder.info(
                                        f"Posting school {index} of {total_ous} "
                                        f"(chunk {school_chunk_index} of {len(chunks)}): "
                                        f"{st.session_state.selected_org_unit_labels.get(ou_uid, ou_uid)}"
                                    )
                                    result = dhis2.push_data_values(
                                        ou_uid,
                                        str(st.session_state.selected_period),
                                        st.session_state.selected_dataset,
                                        ou_chunk,
                                        st.session_state.username,
                                        st.session_state.password
                                    )
                                    response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
                                    imp = result.get('importSummary') or response_block.get('importSummary') or response_block or result
                                    chunk_imported = int(imp.get('importCount', {}).get('imported', 0) or 0)
                                    chunk_updated = int(imp.get('importCount', {}).get('updated', 0) or 0)
                                    chunk_ignored = int(imp.get('importCount', {}).get('ignored', 0) or 0)
                                    chunk_status = str(imp.get('status', 'UNKNOWN') or 'UNKNOWN')
                                    chunk_message = imp.get('description', '') or imp.get('message', '') or ''

                                    imported += chunk_imported
                                    updated += chunk_updated
                                    ignored += chunk_ignored
                                    ou_imported += chunk_imported
                                    ou_updated += chunk_updated
                                    ou_ignored += chunk_ignored

                                    if chunk_status not in ('SUCCESS', 'OK'):
                                        status = 'WARNING'
                                        ou_status = 'WARNING'
                                    if chunk_message:
                                        message_parts.append(chunk_message)
                                        ou_message_parts.append(chunk_message)

                                    done_chunks += 1
                                    progress.progress(
                                        done_chunks / max(total_chunks, 1),
                                        text=(
                                            f"Posted chunk {done_chunks} of {total_chunks} "
                                            f"(school {index}/{total_ous})"
                                        ),
                                    )

                                db.log_sync(
                                    ou_uid,
                                    st.session_state.selected_dataset,
                                    str(st.session_state.selected_period),
                                    len(ou_entries),
                                    ou_imported,
                                    ou_updated,
                                    ou_ignored,
                                    ou_status,
                                    '; '.join(ou_message_parts[:5]),
                                    ''
                                )

                        status_placeholder.success(
                            f"Posting completed. Processed {total_chunks} chunk(s) across {total_ous} school(s)."
                        )

                        message = '; '.join(message_parts[:5])
                        if status in ('SUCCESS', 'OK'):
                            st.session_state['aggregate_post_feedback'] = {
                                'level': 'success',
                                'title': f"Ô£à Successfully posted! Imported: {imported}, Updated: {updated}",
                                'detail': message,
                            }
                            st.session_state['dhis2_cache_buster'] = int(st.session_state.get('dhis2_cache_buster', 0) or 0) + 1
                            load_comparison_data()
                            st.rerun()
                        else:
                            st.session_state['aggregate_post_feedback'] = {
                                'level': 'warning',
                                'title': f"ÔÜá´©Å Post completed with warnings: {status}",
                                'detail': f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}" + (f". {message}" if message else ''),
                            }
                            st.rerun()
                    except Exception as e:
                        db.log_sync(
                            st.session_state.org_unit_uid,
                            st.session_state.selected_dataset,
                            str(st.session_state.selected_period),
                            len(entries), 0, 0, len(entries),
                            'ERROR', str(e), ''
                        )
                        st.session_state['aggregate_post_feedback'] = {
                            'level': 'error',
                            'title': "Failed to post to DHIS2",
                            'detail': str(e),
                        }
                        st.rerun()

    # Sync logs section
    with st.expander("Sync History"):
        display_sync_logs()

def load_comparison_data():
    """Load and compare DHIS2 vs local data"""
    try:
        if st.session_state.data_mode == "Aggregate Forms" and not st.session_state.get('selected_org_units'):
            st.warning("Please select at least one Level 5 school in the sidebar.")
            st.session_state.compare_results = None
            return
        result = compare_data()
        st.session_state.compare_results = result
        st.session_state.edit_mode = False
        st.session_state.edited_values = {}
    except Exception as e:
        st.error(f"Failed to load comparison data: {str(e)}")
        st.session_state.compare_results = None

def compare_data():
    """Compare DHIS2 data with local data"""
    instance_url = str(st.session_state.get('instance_url', '') or '')
    username = st.session_state.username
    password = st.session_state.password
    selected_org_uids = list(st.session_state.get('selected_org_units', []) or [])
    if not selected_org_uids:
        selected_org_uids = [st.session_state.org_unit_uid]
    org_labels = st.session_state.get('selected_org_unit_labels', {}) or {}
    period = str(st.session_state.selected_period)
    dataset_uid = st.session_state.selected_dataset
    
    # Fetch dataset elements
    elements = dhis2.get_dataset_elements(dataset_uid, username, password)
    
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

        if sig not in dedup:
            dedup[sig] = el
            continue
        
        duplicate_rows_removed += 1
        current = dedup[sig]

        # At this stage we haven't fetched org-specific values yet, so choose a stable
        # representative row for duplicate metadata definitions.
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

    cache_buster = int(st.session_state.get('dhis2_cache_buster', 0) or 0)

    def _fetch_org_context(org_uid):
        dhis2_values_uid = load_data_values_cached(
            instance_url,
            org_uid,
            period,
            dataset_uid,
            username,
            password,
            id_scheme='uid',
            cache_buster=cache_buster,
        )
        local_values = db.get_local_values(org_uid, period)
        dhis2_values_name_norm = {}

        fetched_uid_keys = set(dhis2_values_uid.keys())
        unmatched_fetched_uid_keys = fetched_uid_keys - expected_keys
        if unmatched_fetched_uid_keys:
            dhis2_values_name = load_data_values_cached(
                instance_url,
                org_uid,
                period,
                dataset_uid,
                username,
                password,
                id_scheme='name',
                cache_buster=cache_buster,
            )
            for raw_key, value in dhis2_values_name.items():
                de_name, coc_name = (raw_key.split('|', 1) + [''])[:2]
                de_norm = _norm_name(de_name)
                for coc_variant in _coc_variants(coc_name):
                    norm_key = f"{de_norm}|{coc_variant}"
                    dhis2_values_name_norm[norm_key] = value

        return {
            'org_uid': org_uid,
            'org_name': org_labels.get(org_uid, org_uid),
            'dhis2_values_uid': dhis2_values_uid,
            'dhis2_values_name_norm': dhis2_values_name_norm,
            'local_values': local_values,
        }

    worker_count = min(8, max(1, len(selected_org_uids)))
    if worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            org_contexts = list(pool.map(_fetch_org_context, selected_org_uids))
    else:
        org_contexts = [_fetch_org_context(selected_org_uids[0])]

    # Build rows across selected schools.
    rows = []
    for org_ctx in org_contexts:
        org_uid = org_ctx['org_uid']
        org_name = org_ctx['org_name']
        dhis2_values_uid = org_ctx['dhis2_values_uid']
        dhis2_values_name_norm = org_ctx['dhis2_values_name_norm']
        local_values = org_ctx['local_values']
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
                'orgUnitUID': org_uid,
                'orgUnitName': org_name,
                'sectionName': el.get('sectionName', 'Unsectioned') or 'Unsectioned',
                'deName': el['deName'],
                'deUID': el['deUID'],
                'deType': el.get('deType', ''),
                'cocName': el['cocName'],
                'cocUID': el['cocUID'],
                'dhis2Value': dhis2_val,
                'localValue': local_val,
                'status': status,
                'row_key': f"{org_uid}|{key_uid}",
            })
    
    return {
        'rows': rows,
        'duplicate_rows_removed': duplicate_rows_removed,
        'export_map_used': dataset_export_map['exists']
    }

def display_data_entry_interface():
    """Display the data entry interface with table-based editable fields"""
    rows = st.session_state.compare_results['rows']
    is_high_volume = len(rows) > 5000

    if 'status_filter' not in st.session_state:
        st.session_state['status_filter'] = 'Needs Attention' if is_high_volume else 'All'

    feedback = st.session_state.get('aggregate_post_feedback')
    if isinstance(feedback, dict):
        level = str(feedback.get('level', 'info') or 'info').lower()
        title = str(feedback.get('title', '') or '').strip()
        detail = str(feedback.get('detail', '') or '').strip()
        if level == 'success':
            st.success(title or "Last push completed successfully.")
        elif level == 'warning':
            st.warning(title or "Last push completed with warnings.")
        elif level == 'error':
            st.error(title or "Last push failed.")
        else:
            st.info(title or "Last push result available.")
        if detail:
            st.caption(detail)
        if st.button("Dismiss last push message", key="dismiss_aggregate_feedback"):
            st.session_state['aggregate_post_feedback'] = None
            st.rerun()
        st.markdown("---")

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
        st.metric("Matched", matches, delta="Ô£ô")
    with col3:
        st.metric("Differs", differs, delta="ÔÜá´©Å" if differs > 0 else None)
    with col4:
        st.metric("Missing Local", missing_local, delta="­ƒôØ")
    with col5:
        st.metric("Missing DHIS2", missing_dhis2, delta="Ô¼å´©Å")

    cr = st.session_state.compare_results
    diag_parts = []
    if cr.get('export_map_used'):
        diag_parts.append("export file fallback active")
    if cr.get('duplicate_rows_removed', 0) > 0:
        diag_parts.append(f"{cr['duplicate_rows_removed']} duplicate row(s) removed from dataset metadata")
    if diag_parts:
        st.info("Ôä╣´©Å " + " ┬À ".join(diag_parts))

    st.markdown("---")

    # Pre-push review replaces the table when active
    if st.session_state.get('show_push_review'):
        display_push_review()
        return

    # Retry banner for ignored rows from last push
    if st.session_state.get('retry_entries'):
        retry_list = st.session_state['retry_entries']
        st.warning(f"ÔÜá´©Å {len(retry_list)} row(s) were ignored by DHIS2 in the last push and need attention.")
        if st.button("­ƒöä Load ignored rows into Edit Mode", key="load_retry"):
            row_lookup = {r['row_key']: r for r in rows}
            for entry in retry_list:
                key = f"{entry['deUID']}|{entry['cocUID']}"
                if key in row_lookup:
                    st.session_state.edited_values[key] = entry['value']
            st.session_state['_force_all_sections_edit'] = True
            st.session_state['retry_entries'] = []
            st.rerun()
        st.markdown("---")

    # Group by school + section
    sections = {}
    for row in filtered_rows:
        school_label = row.get('orgUnitName', row.get('orgUnitUID', ''))
        section = f"{school_label} :: {row['sectionName']}"
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
        with st.expander(f"📁 {section_name} ({len(section_rows)} fields)", expanded=False):
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
    st.subheader("­ƒôï Review Changes Before Pushing to DHIS2")
    st.markdown("---")

    rows = st.session_state.compare_results['rows']
    changed = []
    for row in rows:
        new_val = st.session_state.edited_values.get(row['row_key'])
        if new_val is not None and new_val != (row['localValue'] or ''):
            changed.append({
                'School': row.get('orgUnitName', row.get('orgUnitUID', '')),
                'Section': row['sectionName'],
                'Data Element': row['deName'],
                'Disaggregation': row['cocName'] or 'Default',
                'Type': row.get('deType', ''),
                'Current DHIS2': row['dhis2Value'] or 'ÔÇö',
                'Old Local': row['localValue'] or 'ÔÇö',
                'New Value': new_val,
            })

    if not changed:
        st.info("No changes detected to push.")
        if st.button("ÔåÉ Go Back"):
            st.session_state['show_push_review'] = False
            st.rerun()
        return

    st.info(f"**{len(changed)} field(s)** will be updated in DHIS2:")
    st.dataframe(pd.DataFrame(changed), use_container_width=True, hide_index=True)
    st.markdown("---")

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("Ô£à Confirm & Push", type="primary"):
            st.session_state['show_push_review'] = False
            st.session_state['_do_push'] = True
    with col2:
        if st.button("ÔåÉ Cancel, Go Back", type="secondary"):
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
        selected_ous = [
            str(uid or '').strip()
            for uid in (st.session_state.get('selected_org_units', []) or [])
            if str(uid or '').strip()
        ]
        if not selected_ous:
            selected_ous = [str(st.session_state.org_unit_uid or '').strip()]

        if len(selected_ous) > 1:
            logs = db.get_sync_logs_for_org_units(selected_ous, limit=300)
            st.caption(f"Showing sync history for {len(selected_ous)} selected school(s).")
        else:
            logs = db.get_sync_logs(selected_ous[0])
        if not logs:
            st.info("No sync logs found")
            return

        df_all = pd.DataFrame(logs)

        # ÔöÇÔöÇ Audit dashboard ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
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
                return 'ÔÇö'
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
                    st.markdown(f"- **{cnt}├ù** `{msg or '(no message)'}`")

        st.markdown("---")

        # ÔöÇÔöÇ Log table ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
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

        # ÔöÇÔöÇ Per-row conflict inspection ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
        conflict_rows = [r for r in logs if r.get('conflictDetails') and r['conflictDetails'] not in ('', '[]', 'None', None)]
        if conflict_rows:
            with st.expander(f"­ƒöì Conflict details ({len(conflict_rows)} sync(s) with conflicts)"):
                for r in conflict_rows[:10]:
                    ts = _fmt_ts(r.get('syncedAt'))
                    st.markdown(f"**{ts}** ÔÇö status: `{r.get('dhis2Status')}`")
                    raw = r.get('conflictDetails', '')
                    try:
                        parsed = json.loads(raw) if raw.startswith('[') else raw
                        if isinstance(parsed, list):
                            for c in parsed:
                                obj = c if isinstance(c, str) else str(c)
                                st.caption(f"ÔÇó {obj}")
                        else:
                            st.caption(raw)
                    except Exception:
                        st.caption(raw)
                    st.markdown("---")

    except Exception as e:
        st.error(f"Failed to load sync logs: {e}")


def load_events_data():
    """Load event rows + stage element metadata for the selected program stage."""
    if not st.session_state.selected_program or not st.session_state.selected_program_stage:
        st.session_state.event_rows = []
        st.session_state.event_element_specs = []
        st.session_state.event_attr_specs = []
        st.session_state.event_original_values = {}
        st.session_state.event_meta = {}
        st.session_state.event_program_meta = {}
        st.session_state.event_attr_columns = []
        st.session_state.event_attr_stats = {}
        st.session_state.event_template_overrides = {}
        st.session_state.event_import_create_changes = []
        return

    year = str(st.session_state.selected_period)
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    elements = dhis2.get_program_stage_elements(
        st.session_state.selected_program_stage,
        st.session_state.username,
        st.session_state.password
    )
    selected_org_uids = list(st.session_state.get('selected_org_units', []) or [st.session_state.org_unit_uid])
    selected_org_uids = [str(x or '').strip() for x in selected_org_uids if str(x or '').strip()]
    org_labels = st.session_state.get('selected_org_unit_labels', {}) or {}

    events = []
    enroll_attr_map = {}
    program_tei_attr_map = {}
    tei_uids_set = set()
    enrollment_uids_set = set()

    for org_uid in selected_org_uids:
        org_events = dhis2.get_events(
            org_uid,
            st.session_state.selected_program,
            st.session_state.selected_program_stage,
            start_date,
            end_date,
            st.session_state.username,
            st.session_state.password
        )
        for ev in org_events:
            if not ev.get('orgUnit'):
                ev['orgUnit'] = org_uid
        events.extend(org_events)

        enrollment_uids_set.update({ev.get('enrollment', '') for ev in org_events if ev.get('enrollment', '')})
        tei_uids_set.update({ev.get('trackedEntityInstance', '') for ev in org_events if ev.get('trackedEntityInstance', '')})

        org_enroll_attr_map = dhis2.get_program_enrollment_attribute_values(
            org_uid,
            st.session_state.selected_program,
            st.session_state.username,
            st.session_state.password
        )
        for tei, attrs in org_enroll_attr_map.items():
            if tei not in enroll_attr_map:
                enroll_attr_map[tei] = {}
            enroll_attr_map[tei].update(attrs)

        org_program_tei_attr_map = dhis2.get_program_tracked_entity_attribute_values(
            org_uid,
            st.session_state.selected_program,
            st.session_state.username,
            st.session_state.password
        )
        for tei, attrs in org_program_tei_attr_map.items():
            if tei not in program_tei_attr_map:
                program_tei_attr_map[tei] = {}
            program_tei_attr_map[tei].update(attrs)

    enrollment_uids = list(enrollment_uids_set)
    enrollment_details = dhis2.get_enrollment_details(
        enrollment_uids,
        st.session_state.username,
        st.session_state.password
    )

    program_attrs = dhis2.get_program_attributes(
        st.session_state.selected_program,
        st.session_state.username,
        st.session_state.password
    )
    program_meta = dhis2.get_program_metadata(
        st.session_state.selected_program,
        st.session_state.username,
        st.session_state.password
    )
    tei_uids = list(tei_uids_set)
    tei_attr_map = dhis2.get_tracked_entity_attribute_values(
        tei_uids,
        st.session_state.username,
        st.session_state.password
    )
    enroll_attr_map_by_tei = dhis2.get_program_enrollment_attribute_values_by_tei(
        tei_uids,
        st.session_state.selected_program,
        st.session_state.username,
        st.session_state.password
    )
    direct_hit_count = 0
    bulk_enroll_hit_count = 0
    per_tei_enroll_hit_count = 0
    program_tei_hit_count = 0

    # Merge fallback values from enrollments where direct TEI fetch is empty/missing.
    for tei in tei_uids:
        direct_vals = tei_attr_map.get(tei, {})
        enroll_vals = enroll_attr_map.get(tei, {})
        enroll_vals_by_tei = enroll_attr_map_by_tei.get(tei, {})
        program_tei_vals = program_tei_attr_map.get(tei, {})
        combined_enroll_vals = dict(enroll_vals)
        combined_enroll_vals.update(enroll_vals_by_tei)
        combined_enroll_vals.update(program_tei_vals)
        if direct_vals:
            direct_hit_count += 1
        if enroll_vals:
            bulk_enroll_hit_count += 1
        if enroll_vals_by_tei:
            per_tei_enroll_hit_count += 1
        if program_tei_vals:
            program_tei_hit_count += 1
        if not direct_vals and combined_enroll_vals:
            tei_attr_map[tei] = dict(combined_enroll_vals)
        elif direct_vals and combined_enroll_vals:
            merged = dict(combined_enroll_vals)
            merged.update(direct_vals)
            tei_attr_map[tei] = merged

    attr_name_counts = {}
    for a in program_attrs:
        name = a.get('attrName', a.get('attrUID', 'Attribute'))
        attr_name_counts[name] = attr_name_counts.get(name, 0) + 1

    attr_specs = []
    for a in program_attrs:
        base_name = a.get('attrName', a.get('attrUID', 'Attribute'))
        if attr_name_counts.get(base_name, 0) > 1:
            col = f"Attr: {base_name} [{a.get('attrUID', '')[:6]}]"
        else:
            col = f"Attr: {base_name}"
        attr_specs.append({
            'attrUID': a.get('attrUID', ''),
            'attrName': base_name,
            'attrType': a.get('attrType', ''),
            'mandatory': bool(a.get('mandatory', False)),
            'column': col,
        })

    # Build stable, unique column labels for event value columns.
    name_counts = {}
    specs = []
    for el in elements:
        de_name = el.get('deName', el['deUID'])
        name_counts[de_name] = name_counts.get(de_name, 0) + 1
    for el in elements:
        de_name = el.get('deName', el['deUID'])
        if name_counts.get(de_name, 0) > 1:
            column = f"{de_name} [{el['deUID'][:6]}]"
        else:
            column = de_name
        specs.append({
            'deUID': el['deUID'],
            'deName': de_name,
            'deType': el.get('deType', ''),
            'column': column,
        })

    rows = []
    original_values = {}
    event_meta = {}
    rows_with_attr_values = 0
    def _uid(val):
        """Extract UID string from either a plain string or a DHIS2 {id:...} object."""
        if isinstance(val, dict):
            return val.get('id') or val.get('uid') or ''
        return str(val or '').strip()

    for ev in events:
        event_id = ev.get('event', '')
        tei_uid = ev.get('trackedEntityInstance', '')
        enrollment_id = ev.get('enrollment', '')
        event_org_uid = _uid(ev.get('orgUnit', st.session_state.org_unit_uid))
        enrollment_date_raw = enrollment_details.get(enrollment_id, {}).get('enrollmentDate', '')
        enrollment_date, _ = _normalize_event_date(enrollment_date_raw)
        value_map = {
            dv.get('dataElement', ''): str(dv.get('value', '') or '')
            for dv in ev.get('dataValues', [])
            if dv.get('dataElement', '')
        }

        row = {
            'Template Row ID': event_id,
            'Event ID': event_id,
            'Org Unit UID': event_org_uid,
            'Org Unit': org_labels.get(event_org_uid, event_org_uid),
            'Person ID': tei_uid,
            'Event Date': ev.get('eventDate', ''),
            'Enrollment Date': enrollment_date,
            'Status': ev.get('status', ''),
        }
        attr_values = tei_attr_map.get(tei_uid, {})
        if attr_values:
            rows_with_attr_values += 1
        for a in attr_specs:
            row[a['column']] = attr_values.get(a['attrUID'], '')
        for spec in specs:
            row[spec['column']] = value_map.get(spec['deUID'], '')
        rows.append(row)

        original_values[event_id] = {
            spec['deUID']: value_map.get(spec['deUID'], '')
            for spec in specs
        }
        event_meta[event_id] = {
            'event': event_id,
            'program': _uid(ev.get('program', st.session_state.selected_program)),
            'programStage': _uid(ev.get('programStage', st.session_state.selected_program_stage)),
            'orgUnit': event_org_uid,
            'orgUnitName': org_labels.get(event_org_uid, event_org_uid),
            'trackedEntityInstance': tei_uid,
            'enrollment': enrollment_id,
            'enrollmentDate': enrollment_date,
            'eventDate': ev.get('eventDate', ''),
            'completedDate': ev.get('completedDate', ''),
            'status': ev.get('status', 'ACTIVE'),
        }

    st.session_state.event_rows = rows
    st.session_state.event_element_specs = specs
    st.session_state.event_attr_specs = attr_specs
    st.session_state.event_original_values = original_values
    st.session_state.event_meta = event_meta
    st.session_state.event_program_meta = program_meta
    st.session_state.event_attr_columns = [a['column'] for a in attr_specs]
    st.session_state.event_attr_stats = {
        'events': len(events),
        'program_attributes': len(attr_specs),
        'tei_ids': len(tei_uids),
        'rows_with_values': rows_with_attr_values,
        'direct_hits': direct_hit_count,
        'bulk_enrollment_hits': bulk_enroll_hit_count,
        'per_tei_enrollment_hits': per_tei_enroll_hit_count,
        'program_tei_hits': program_tei_hit_count,
    }
    st.session_state.event_template_overrides = {}
    st.session_state.event_import_create_changes = []
    st.session_state.event_show_review = False
    st.session_state.event_has_unsaved_edits = False


def _validate_event_changes(changes):
    """Validate staged event changes against data element value types."""
    clean, issues = [], []
    for change in changes:
        change_type = change.get('changeType', 'DATA_VALUE')
        de_type = change.get('deType', '')
        val = str(change.get('newValue', '')).strip()
        issue = None

        if change_type in ('EVENT_DATE', 'ENROLLMENT_DATE', 'CREATE_EVENT_DATE', 'CREATE_ENROLLMENT_DATE'):
            normalized_date, parse_issue = _normalize_event_date(val)
            if parse_issue:
                issue = parse_issue
            elif not normalized_date:
                issue = 'Date value cannot be empty'
            else:
                change['newValue'] = normalized_date
                val = normalized_date
            if change_type == 'ENROLLMENT_DATE' and not str(change.get('enrollmentId', '') or '').strip():
                issue = 'Missing enrollment ID for enrollment date update'

        elif change_type == 'EVENT_STATUS':
            if val.upper() not in ('ACTIVE', 'COMPLETED'):
                issue = f"Status must be ACTIVE or COMPLETED (got '{val}')"
            else:
                change['newValue'] = val.upper()

        elif val == '':
            issue = 'Empty value will be skipped by DHIS2'
        elif de_type in _NUMERIC_TYPES:
            try:
                num = float(val)
                if de_type == 'INTEGER_POSITIVE' and num <= 0:
                    issue = f"Must be > 0 (got {val})"
                elif de_type == 'INTEGER_ZERO_OR_POSITIVE' and num < 0:
                    issue = f"Must be ÔëÑ 0 (got {val})"
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
            issues.append({
                'Event ID': change.get('eventId', ''),
                'Data Element': (
                    'Event Date' if change_type in ('EVENT_DATE', 'CREATE_EVENT_DATE')
                    else 'Enrollment Date' if change_type in ('ENROLLMENT_DATE', 'CREATE_ENROLLMENT_DATE')
                    else change.get('deName', change.get('deUID', ''))
                ),
                'Value': val,
                'Type': de_type,
                'Issue': issue,
            })
        else:
            clean.append(change)
    return clean, issues


def _normalize_event_date(value):
    """Normalize event date input to YYYY-MM-DD for reliable comparison and push payloads."""
    text = str(value or '').strip()
    if not text:
        return '', None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return text, None
    if re.fullmatch(r'\d{2}-\d{2}-\d{4}', text):
        try:
            return datetime.strptime(text, '%d-%m-%Y').date().isoformat(), None
        except ValueError:
            return '', f"Expected a valid calendar date (got '{text}')"

    iso_text = text.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(iso_text)
        return dt.date().isoformat(), None
    except ValueError:
        return '', f"Expected date in YYYY-MM-DD, DD-MM-YYYY, or ISO datetime format (got '{text}')"


def _build_create_template_rows(attr_specs, element_specs, count=3):
    """Append a few blank rows to support CSV/UI creation of new staff records."""
    rows = []
    for index in range(1, count + 1):
        row = {
            'Template Row ID': f'__NEW__{index}',
            'Event ID': '',
            'Person ID': '',
            'Event Date': '',
            'Enrollment Date': '',
            'Status': 'ACTIVE',
        }
        for spec in attr_specs:
            row[spec['column']] = ''
        for spec in element_specs:
            row[spec['column']] = ''
        rows.append(row)
    return rows


def _row_has_create_values(row, attr_specs, element_specs):
    for key in ('Event Date', 'Enrollment Date'):
        if str(row.get(key, '') or '').strip():
            return True
    for spec in attr_specs:
        if str(row.get(spec['column'], '') or '').strip():
            return True
    for spec in element_specs:
        if str(row.get(spec['column'], '') or '').strip():
            return True
    return False


def push_events_to_dhis2(changes):
    """Push validated event data value changes to DHIS2."""
    clean_changes, issues = _validate_event_changes(changes)
    if issues:
        st.warning(f"ÔÜá´©Å {len(issues)} event value(s) failed validation and were removed from this push:")
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
    if not clean_changes:
        st.error("No valid event changes to push.")
        return False

    create_changes = [c for c in clean_changes if str(c.get('changeType', '')).startswith('CREATE_')]
    event_changes = [
        c for c in clean_changes
        if c.get('changeType') not in ('ENROLLMENT_DATE',) and not str(c.get('changeType', '')).startswith('CREATE_')
    ]
    enrollment_changes = [c for c in clean_changes if c.get('changeType') == 'ENROLLMENT_DATE']

    if len(list(st.session_state.get('selected_org_units', []) or [])) > 1 and create_changes:
        st.warning("CREATE_* rows are skipped in multi-school Events mode. Select one school to create new staff/event rows.")
        create_changes = []

    grouped = {}
    for change in event_changes:
        event_id = change['eventId']
        meta = st.session_state.event_meta.get(event_id, {})

        def _uid_str(val, fallback=''):
            if isinstance(val, dict):
                return val.get('id') or val.get('uid') or fallback
            return str(val or fallback).strip()

        if event_id not in grouped:
            # Pre-populate ALL existing data values so mandatory fields aren't wiped by PUT.
            # DHIS2 PUT replaces the entire event; only sending changed values removes required fields.
            existing_dv = st.session_state.event_original_values.get(event_id, {})
            base_data_values = {
                de_uid: str(val)
                for de_uid, val in existing_dv.items()
            }
            grouped[event_id] = {
                'event': event_id,
                'program': _uid_str(meta.get('program'), st.session_state.selected_program),
                'programStage': _uid_str(meta.get('programStage'), st.session_state.selected_program_stage),
                'orgUnit': _uid_str(meta.get('orgUnit'), st.session_state.org_unit_uid),
                'trackedEntityInstance': str(meta.get('trackedEntityInstance', '') or '').strip(),
                'enrollment': str(meta.get('enrollment', '') or '').strip(),
                'eventDate': meta.get('eventDate', ''),
                'completedDate': meta.get('completedDate', ''),
                'status': meta.get('status', 'ACTIVE'),
                '_dv_map': base_data_values,  # keyed by deUID for easy override
            }
        if change.get('changeType') == 'EVENT_DATE':
            grouped[event_id]['eventDate'] = str(change.get('newValue', '')).strip()
        elif change.get('changeType') == 'EVENT_STATUS':
            grouped[event_id]['status'] = str(change.get('newValue', '')).strip().upper()
        else:
            # Override this specific data element with the new value
            grouped[event_id]['_dv_map'][change['deUID']] = str(change['newValue']).strip()

    # Convert _dv_map to the dataValues list DHIS2 expects
    for ev_payload in grouped.values():
        dv_map = ev_payload.pop('_dv_map', {})
        ev_payload['dataValues'] = [
            {'dataElement': de_uid, 'value': val}
            for de_uid, val in dv_map.items()
        ]

    updates = list(grouped.values())

    enrollment_grouped = {}
    for change in enrollment_changes:
        enrollment_id = str(change.get('enrollmentId', '') or '').strip()
        if not enrollment_id:
            continue
        event_id = change.get('eventId', '')
        meta = st.session_state.event_meta.get(event_id, {})
        enrollment_grouped[enrollment_id] = {
            'enrollment': enrollment_id,
            'program': meta.get('program', st.session_state.selected_program),
            'orgUnit': meta.get('orgUnit', st.session_state.org_unit_uid),
            'trackedEntityInstance': meta.get('trackedEntityInstance', ''),
            'status': meta.get('status', 'ACTIVE'),
            'enrollmentDate': str(change.get('newValue', '')).strip(),
        }

    enrollment_updates = list(enrollment_grouped.values())

    create_groups = {}
    for change in create_changes:
        row_id = str(change.get('templateRowId', '') or '')
        if not row_id:
            continue
        create_groups.setdefault(row_id, []).append(change)

    create_issues = []
    valid_create_groups = []
    attr_spec_by_uid = {spec['attrUID']: spec for spec in st.session_state.get('event_attr_specs', [])}
    program_meta = st.session_state.get('event_program_meta', {})
    for row_id, row_changes in create_groups.items():
        person_name = next((str(ch.get('personName', '') or '') for ch in row_changes if ch.get('personName')), '')
        existing_person_id = next((str(ch.get('newValue', '') or '').strip() for ch in row_changes if ch.get('changeType') == 'CREATE_PERSON_ID'), '')
        event_date = next((str(ch.get('newValue', '') or '') for ch in row_changes if ch.get('changeType') == 'CREATE_EVENT_DATE'), '')
        enrollment_date = next((str(ch.get('newValue', '') or '') for ch in row_changes if ch.get('changeType') == 'CREATE_ENROLLMENT_DATE'), '')
        event_only = any(bool(ch.get('eventOnly', False)) for ch in row_changes)
        attr_changes = [ch for ch in row_changes if ch.get('changeType') == 'CREATE_ATTRIBUTE']
        data_value_changes = [ch for ch in row_changes if ch.get('changeType') == 'CREATE_DATA_VALUE']

        missing_required = []
        for spec in st.session_state.get('event_attr_specs', []):
            if not spec.get('mandatory'):
                continue
            value = next((str(ch.get('newValue', '') or '').strip() for ch in attr_changes if ch.get('attrUID') == spec.get('attrUID')), '')
            if not value:
                missing_required.append(spec['attrName'])

        if not existing_person_id:
            if not program_meta.get('trackedEntityType'):
                create_issues.append({'Event ID': '', 'Data Element': 'Program', 'Value': row_id, 'Type': 'CREATE', 'Issue': 'Program tracked entity type is missing'})
                continue
            if missing_required:
                create_issues.append({'Event ID': '', 'Data Element': 'Attributes', 'Value': person_name or row_id, 'Type': 'CREATE', 'Issue': f"Missing required attributes: {', '.join(missing_required)}"})
                continue
        if not enrollment_date and not existing_person_id:
            create_issues.append({'Event ID': '', 'Data Element': 'Enrollment Date', 'Value': person_name or row_id, 'Type': 'CREATE', 'Issue': 'Enrollment Date is required for new rows'})
            continue
        if not event_date:
            create_issues.append({'Event ID': '', 'Data Element': 'Event Date', 'Value': person_name or row_id, 'Type': 'CREATE', 'Issue': 'Event Date is required for new rows'})
            continue
        if not data_value_changes:
            create_issues.append({'Event ID': '', 'Data Element': 'Stage Fields', 'Value': person_name or row_id, 'Type': 'CREATE', 'Issue': 'At least one stage field is required for a new event'})
            continue

        valid_create_groups.append({
            'rowId': row_id,
            'personName': person_name,
            'personId': existing_person_id,
            'eventDate': event_date,
            'enrollmentDate': enrollment_date,
            'eventOnly': event_only,
            'attributes': [
                {
                    'attribute': ch['attrUID'],
                    'value': str(ch.get('newValue', '')).strip(),
                }
                for ch in attr_changes if str(ch.get('newValue', '')).strip()
            ],
            'dataValues': [
                {
                    'dataElement': ch['deUID'],
                    'value': str(ch.get('newValue', '')).strip(),
                }
                for ch in data_value_changes if str(ch.get('newValue', '')).strip()
            ],
        })

    if create_issues:
        st.warning(f"ÔÜá´©Å {len(create_issues)} new-row issue(s) were found and those rows were skipped:")
        st.dataframe(pd.DataFrame(create_issues), use_container_width=True, hide_index=True)

    try:
        result = {'status': 'SUCCESS', 'message': '', 'response': {'importCount': {'imported': 0, 'updated': 0, 'ignored': 0}}}
        if updates:
            result = dhis2.push_event_updates(
                updates,
                st.session_state.username,
                st.session_state.password
            )

        enrollment_result = {'status': 'SUCCESS', 'message': '', 'response': {'importCount': {'imported': 0, 'updated': 0, 'ignored': 0}}}
        if enrollment_updates:
            enrollment_result = dhis2.push_enrollment_updates(
                enrollment_updates,
                st.session_state.username,
                st.session_state.password
            )

        created_rows = 0
        unverified_created_rows = 0

        remaining_create_groups = list(valid_create_groups)
        bulk_bundle_candidates = [g for g in valid_create_groups if not str(g.get('personId', '') or '').strip()]
        if bulk_bundle_candidates:
            bulk_bundle_result = dhis2.create_tracker_bundle_bulk(
                tracked_entity_type_uid=program_meta.get('trackedEntityType', ''),
                program_uid=st.session_state.selected_program,
                program_stage_uid=st.session_state.selected_program_stage,
                org_unit_uid=st.session_state.org_unit_uid,
                create_groups=bulk_bundle_candidates,
                username=st.session_state.username,
                password=st.session_state.password,
            )
            if bulk_bundle_result.get('status') in ('OK', 'WARNING'):
                created_rows += len(bulk_bundle_candidates)
                remaining_create_groups = [
                    g for g in valid_create_groups
                    if str(g.get('personId', '') or '').strip()
                ]
                if bulk_bundle_result.get('errors'):
                    st.warning(f"ÔÜá´©Å Bulk tracker bundle warnings: {'; '.join(bulk_bundle_result['errors'][:3])}")

        for create_group in remaining_create_groups:
            tei_uid = str(create_group.get('personId', '') or '').strip()
            person_label = create_group['personName'] or create_group['rowId']
            event_only = bool(create_group.get('eventOnly', False))

            # ÔöÇÔöÇ If TEI already exists, resolve enrollment then create event only ÔöÇÔöÇ
            if tei_uid:
                existing_enrollment_uid = dhis2.get_existing_enrollment_for_tei(
                    st.session_state.selected_program,
                    tei_uid,
                    st.session_state.org_unit_uid,
                    st.session_state.username,
                    st.session_state.password
                )
                if existing_enrollment_uid:
                    # Only create the event under the existing enrollment
                    try:
                        event_result_create = dhis2.create_event(
                            st.session_state.selected_program,
                            st.session_state.selected_program_stage,
                            tei_uid,
                            existing_enrollment_uid,
                            st.session_state.org_unit_uid,
                            create_group['eventDate'],
                            create_group['dataValues'],
                            st.session_state.username,
                            st.session_state.password
                        )
                        event_uid = dhis2._extract_import_reference(event_result_create)
                    except Exception as ev_exc:
                        import requests as _req
                        event_uid = ''
                        is_timeout = isinstance(ev_exc, _req.exceptions.Timeout)
                        if is_timeout:
                            unverified_created_rows += 1
                            created_rows += 1
                            st.warning(f"Event create not verifiable yet for {person_label} (timeout).")
                            continue
                        raise ValueError(f"Failed to create event for {person_label}: {ev_exc}")
                    if not event_uid:
                        raise ValueError(f"Failed to create event for {person_label}: no event UID returned")
                    created_rows += 1
                    continue
                # No existing enrollment ÔÇö fall through to create enrollment + event below
                if event_only:
                    st.warning(f"Skipped event-only import row for {person_label}: no enrollment found for Person ID {tei_uid}.")
                    continue

            # ÔöÇÔöÇ Try new single-call /tracker bundle (TEI + enrollment + event) ÔöÇÔöÇ
            if event_only:
                st.warning(f"Skipped event-only import row for {person_label}: Person ID is missing.")
                continue

            bundle_result = dhis2.create_tracker_bundle(
                tracked_entity_type_uid=program_meta.get('trackedEntityType', ''),
                program_uid=st.session_state.selected_program,
                program_stage_uid=st.session_state.selected_program_stage,
                org_unit_uid=st.session_state.org_unit_uid,
                enrollment_date=create_group['enrollmentDate'],
                event_date=create_group['eventDate'],
                attributes=create_group['attributes'],
                data_values=create_group['dataValues'],
                username=st.session_state.username,
                password=st.session_state.password,
                existing_tei_uid='',  # Always create full bundle here (TEI is new or has no enrollment)
            )

            if bundle_result['status'] == 'NOT_FOUND':
                bundle_result = None  # Server doesn't support /tracker ÔÇö use old flow below
            elif bundle_result['status'] in ('OK', 'WARNING'):
                if bundle_result['errors']:
                    st.warning(f"ÔÜá´©Å {person_label}: {'; '.join(bundle_result['errors'][:3])}")
                created_rows += 1
                continue
            else:
                bundle_result = None  # /tracker returned error ÔÇö fall back to old flow

            # ÔöÇÔöÇ Fallback: old sequential flow (/trackedEntityInstances ÔåÆ /enrollments ÔåÆ /events) ÔöÇÔöÇ
            if not tei_uid:
                tei_result = dhis2.create_tracked_entity_instance(
                    program_meta.get('trackedEntityType', ''),
                    st.session_state.org_unit_uid,
                    create_group['attributes'],
                    st.session_state.username,
                    st.session_state.password
                )
                tei_uid = dhis2._extract_import_reference(tei_result)
                if not tei_uid:
                    raise ValueError(f"Failed to create tracked entity for {person_label}")

            try:
                enrollment_result_create = dhis2.create_enrollment(
                    st.session_state.selected_program,
                    tei_uid,
                    st.session_state.org_unit_uid,
                    create_group['enrollmentDate'],
                    create_group['enrollmentDate'],
                    st.session_state.username,
                    st.session_state.password
                )
                enrollment_uid = dhis2._extract_import_reference(enrollment_result_create)
            except Exception:
                enrollment_uid = ''
            if not enrollment_uid:
                enrollment_uid = dhis2.get_existing_enrollment_for_tei(
                    st.session_state.selected_program,
                    tei_uid,
                    st.session_state.org_unit_uid,
                    st.session_state.username,
                    st.session_state.password
                )
            if not enrollment_uid:
                raise ValueError(f"Failed to create or resolve enrollment for {person_label}")

            try:
                event_result_create = dhis2.create_event(
                    st.session_state.selected_program,
                    st.session_state.selected_program_stage,
                    tei_uid,
                    enrollment_uid,
                    st.session_state.org_unit_uid,
                    create_group['eventDate'],
                    create_group['dataValues'],
                    st.session_state.username,
                    st.session_state.password
                )
                event_uid = dhis2._extract_import_reference(event_result_create)
                create_event_error = None
            except Exception as create_ev_exc:
                import requests as _req
                event_uid = ''
                is_timeout = isinstance(create_ev_exc, _req.exceptions.Timeout)
                create_event_error = ('Request timed out ÔÇö DHIS2 may still have created the event. ' if is_timeout else '') + str(create_ev_exc)
            lookup_error = ''
            if not event_uid:
                try:
                    event_uid = dhis2.get_existing_event_for_enrollment(
                        st.session_state.selected_program_stage,
                        enrollment_uid,
                        st.session_state.username,
                        st.session_state.password
                    )
                except Exception as verify_exc:
                    event_uid = ''
                    lookup_error = str(verify_exc)
            if not event_uid:
                transient_error_text = f"{create_event_error or ''} {lookup_error or ''}".lower()
                is_transient = (
                    'timed out' in transient_error_text
                    or 'timeout' in transient_error_text
                    or '504' in transient_error_text
                    or 'gateway time-out' in transient_error_text
                    or 'gateway timeout' in transient_error_text
                )
                if is_transient:
                    unverified_created_rows += 1
                    created_rows += 1
                    st.warning(
                        f"Event create not verifiable yet for {person_label} due to a temporary server timeout (504)."
                    )
                    continue
                details = [d for d in (create_event_error, lookup_error) if d]
                detail = f": {' | '.join(details)}" if details else ""
                raise ValueError(f"Failed to create event for {person_label}{detail}")
            created_rows += 1

        response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
        imp = result.get('importSummary') or response_block.get('importSummary') or response_block or result
        import_count = imp.get('importCount', {}) if isinstance(imp, dict) else {}
        imported = int(import_count.get('imported', 0) or 0)
        updated = int(import_count.get('updated', 0) or 0)
        ignored = int(import_count.get('ignored', 0) or 0)
        status = imp.get('status', result.get('status', 'UNKNOWN')) if isinstance(imp, dict) else result.get('status', 'UNKNOWN')
        message = ''
        if isinstance(imp, dict):
            message = imp.get('description', '') or imp.get('message', '') or ''
        if not message:
            message = result.get('message', '') or result.get('description', '') or ''

        # Fallback parsing for event import summaries if importCount is missing.
        if imported == 0 and updated == 0 and ignored == 0:
            summaries = result.get('importSummaries', [])
            if isinstance(summaries, list) and summaries:
                ignored = sum(1 for s in summaries if str(s.get('status', '')).upper() == 'ERROR')
                updated = len(summaries) - ignored

        enrollment_response = enrollment_result.get('response', {}) if isinstance(enrollment_result.get('response', {}), dict) else {}
        enrollment_imp = enrollment_result.get('importSummary') or enrollment_response.get('importSummary') or enrollment_response or enrollment_result
        enrollment_count = enrollment_imp.get('importCount', {}) if isinstance(enrollment_imp, dict) else {}
        enrollment_updated = int(enrollment_count.get('updated', 0) or 0)
        enrollment_ignored = int(enrollment_count.get('ignored', 0) or 0)

        total_updated = updated + enrollment_updated + created_rows
        total_ignored = ignored + enrollment_ignored
        final_status = status if str(status).upper() not in ('SUCCESS', 'OK') else enrollment_imp.get('status', status) if isinstance(enrollment_imp, dict) else status

        db.log_sync(
            st.session_state.org_unit_uid,
            f"EVENTS:{st.session_state.selected_program_stage}",
            str(st.session_state.selected_period),
            len(clean_changes),
            imported,
            total_updated,
            total_ignored,
            final_status,
            message,
            ''
        )

        if str(final_status).upper() in ('SUCCESS', 'OK') and total_ignored == 0:
            st.success(
                "Ô£à Events synced successfully. "
                f"Updated events: {len(updates)}, updated enrollments: {len(enrollment_updates)}, created staff rows: {created_rows}"
            )
            if unverified_created_rows > 0:
                st.warning(
                    f"{unverified_created_rows} newly created row(s) could not be verified immediately because DHIS2 returned timeout/504 on verification."
                )
            return True

        st.warning(f"ÔÜá´©Å Event sync completed with warnings: {final_status}")
        st.info(f"Updated: {total_updated}, Ignored: {total_ignored}")
        return False
    except Exception as e:
        db.log_sync(
            st.session_state.org_unit_uid,
            f"EVENTS:{st.session_state.selected_program_stage}",
            str(st.session_state.selected_period),
            len(clean_changes),
            0,
            0,
            len(clean_changes),
            'ERROR',
            str(e),
            ''
        )
        st.error(f"Sync failed: {e}")
        return False


def display_events_interface():
    """Display event data table, edit review, and push flow."""
    st.subheader("Events Data Entry")

    if not st.session_state.selected_program or not st.session_state.selected_program_stage:
        st.info("Select a Program and Program Stage in the sidebar to fetch events.")
        return

    events_multi_scope = len(list(st.session_state.get('selected_org_units', []) or [])) > 1

    top_left, top_right = st.columns([1, 4])
    with top_left:
        if st.button("Refresh Events", key="refresh_events", type="primary"):
            with st.spinner("Loading events from DHIS2..."):
                load_events_data()
                st.session_state['events_editor_rev'] += 1
                st.rerun()

    if not st.session_state.event_rows:
        if events_multi_scope:
            st.info("No existing events found for the selected schools, program stage, and period.")
        else:
            st.info("No existing events found for this school, program stage, and period. You can still create new staff enrollment/event rows below.")

    attr_stats = st.session_state.get('event_attr_stats', {})
    if st.session_state.get('event_attr_columns'):
        st.caption(
            f"Program attributes loaded: {attr_stats.get('program_attributes', 0)} ┬À "
            f"events: {attr_stats.get('events', 0)} ┬À "
            f"persons: {attr_stats.get('tei_ids', 0)} ┬À "
            f"rows with attribute values: {attr_stats.get('rows_with_values', 0)} ┬À "
            f"direct TEI hits: {attr_stats.get('direct_hits', 0)} ┬À "
            f"bulk enrollment hits: {attr_stats.get('bulk_enrollment_hits', 0)} ┬À "
            f"per-TEI enrollment hits: {attr_stats.get('per_tei_enrollment_hits', 0)} ┬À "
            f"program TEI hits: {attr_stats.get('program_tei_hits', 0)}"
        )

    st.markdown("---")
    st.subheader("Events Template Export/Import")

    specs = st.session_state.event_element_specs
    attr_specs = st.session_state.get('event_attr_specs', [])
    column_to_spec = {s['column']: s for s in specs}
    stage_columns = [s['column'] for s in specs]
    attr_columns = [s['column'] for s in attr_specs]
    template_editable_columns = attr_columns + stage_columns + ['Person ID', 'Event Date', 'Enrollment Date']

    def _normalize_event_compare_value(raw_value, de_type=''):
        val = '' if pd.isna(raw_value) else str(raw_value).strip()
        if de_type in _BOOL_TYPES:
            low = val.lower()
            if low in ('true', '1', 'yes'):
                return 'true'
            if low in ('false', '0', 'no'):
                return 'false'
        return val

    # Build working table and apply any imported template overrides.
    if events_multi_scope:
        df = pd.DataFrame(st.session_state.event_rows)
    else:
        df = pd.DataFrame(st.session_state.event_rows + _build_create_template_rows(attr_specs, specs))
    overrides = st.session_state.get('event_template_overrides', {})
    if overrides:
        for idx in df.index:
            row_id = str(df.at[idx, 'Template Row ID'])
            row_overrides = overrides.get(row_id, {})
            for col_name, value in row_overrides.items():
                if col_name in df.columns:
                    df.at[idx, col_name] = value

    tcol1, tcol2, tcol3 = st.columns([1, 1, 2])
    with tcol1:
        if st.button("Download Events Template (CSV)", key="download_events_template"):
            csv = df.to_csv(index=False).encode('utf-8')
            scope_tag = "multi" if events_multi_scope else (st.session_state.school_code or 'school')
            st.download_button(
                label="Download Events CSV",
                data=csv,
                file_name=f"events_template_{scope_tag}_{st.session_state.selected_period}.csv",
                mime='text/csv',
                key="download_events_template_file"
            )
    with tcol2:
        uploaded_events_file = st.file_uploader(
            "Upload Events Template (CSV)",
            type=["csv"],
            key="events_template_upload"
        )
        if uploaded_events_file is not None:
            try:
                current_upload_sig = f"{uploaded_events_file.name}:{uploaded_events_file.size}"
                if current_upload_sig == st.session_state.get('event_last_uploaded_template_sig', ''):
                    st.caption("Uploaded template already applied.")
                    imported_df = None
                else:
                    imported_df = pd.read_csv(uploaded_events_file, dtype=str)
                    st.session_state['event_last_uploaded_template_sig'] = current_upload_sig

                if imported_df is None:
                    pass
                elif 'Template Row ID' not in imported_df.columns:
                    st.error("CSV missing required column: Template Row ID")
                else:
                    valid_event_ids = {str(r.get('Event ID', '')) for r in st.session_state.event_rows if str(r.get('Event ID', '')).strip()}
                    valid_row_ids = {str(r.get('Template Row ID', '')) for _, r in df.iterrows()}
                    imported_editable_cols = [c for c in imported_df.columns if c in template_editable_columns]
                    imported_stage_cols = [c for c in imported_editable_cols if c in stage_columns]
                    has_event_date_col = 'Event Date' in imported_editable_cols
                    has_enrollment_date_col = 'Enrollment Date' in imported_editable_cols
                    if not imported_editable_cols:
                        st.error("CSV contains no editable stage/date columns for this selected program stage.")
                    else:
                        new_overrides = {}
                        imported_create_changes = []
                        skipped_unknown_events = 0
                        skipped_create_rows_multi = 0
                        skipped_unknown_missing_person = 0
                        skipped_unknown_missing_values = 0
                        skipped_unknown_invalid_date = 0
                        non_stage_changes = 0
                        invalid_event_date_rows = 0
                        invalid_enrollment_date_rows = 0
                        original_values = st.session_state.event_original_values
                        current_row_map = {
                            str(row.get('Template Row ID', '') or ''): row
                            for _, row in df.iterrows()
                        }
                        non_stage_cols = [
                            c for c in imported_df.columns
                            if c not in imported_editable_cols and c != 'Event ID'
                        ]
                        for _, csv_row in imported_df.iterrows():
                            row_id = str(csv_row.get('Template Row ID', '') or '').strip()
                            event_id = str(csv_row.get('Event ID', '') or '').strip()
                            if not row_id:
                                continue
                            if row_id not in valid_row_ids:
                                skipped_unknown_events += 1
                                continue

                            is_create_row = str(row_id).startswith('__NEW__')
                            if is_create_row and events_multi_scope:
                                skipped_create_rows_multi += 1
                                continue
                            current_row = current_row_map.get(row_id)
                            if current_row is not None:
                                for col_name in non_stage_cols:
                                    if col_name not in current_row.index:
                                        continue
                                    raw_non_stage = csv_row.get(col_name, '')
                                    val_non_stage = '' if pd.isna(raw_non_stage) else str(raw_non_stage).strip()
                                    cur_non_stage = str(current_row.get(col_name, '') or '').strip()
                                    if val_non_stage != cur_non_stage:
                                        non_stage_changes += 1

                            if is_create_row and not _row_has_create_values(csv_row, attr_specs, specs):
                                continue

                            if is_create_row:
                                person_id_csv = str(csv_row.get('Person ID', '') or '').strip()
                                if person_id_csv:
                                    new_overrides.setdefault(row_id, {})['Person ID'] = person_id_csv
                                for col_name in attr_columns:
                                    raw_val = csv_row.get(col_name, '')
                                    val = '' if pd.isna(raw_val) else str(raw_val).strip()
                                    if val:
                                        new_overrides.setdefault(row_id, {})[col_name] = val
                                for col_name in imported_stage_cols:
                                    raw_val = csv_row.get(col_name, '')
                                    val = '' if pd.isna(raw_val) else str(raw_val).strip()
                                    if val:
                                        new_overrides.setdefault(row_id, {})[col_name] = val
                                if has_event_date_col:
                                    raw_date = csv_row.get('Event Date', '')
                                    new_date_raw = '' if pd.isna(raw_date) else str(raw_date).strip()
                                    if new_date_raw:
                                        new_date, parse_issue = _normalize_event_date(new_date_raw)
                                        if parse_issue:
                                            invalid_event_date_rows += 1
                                        else:
                                            new_overrides.setdefault(row_id, {})['Event Date'] = new_date
                                if has_enrollment_date_col:
                                    raw_enrollment_date = csv_row.get('Enrollment Date', '')
                                    new_enrollment_date_raw = '' if pd.isna(raw_enrollment_date) else str(raw_enrollment_date).strip()
                                    if new_enrollment_date_raw:
                                        new_enrollment_date, parse_issue = _normalize_event_date(new_enrollment_date_raw)
                                        if parse_issue:
                                            invalid_enrollment_date_rows += 1
                                        else:
                                            new_overrides.setdefault(row_id, {})['Enrollment Date'] = new_enrollment_date
                                continue

                            if event_id not in valid_event_ids:
                                # Cross-period import support: convert unknown Event IDs into
                                # event-only creates for existing persons (no person/enrollment creation).
                                person_id_csv = str(csv_row.get('Person ID', '') or '').strip()
                                if not person_id_csv:
                                    skipped_unknown_missing_person += 1
                                    skipped_unknown_events += 1
                                    continue

                                raw_date = csv_row.get('Event Date', '')
                                new_date_raw = '' if pd.isna(raw_date) else str(raw_date).strip()
                                new_date, parse_issue = _normalize_event_date(new_date_raw)
                                if parse_issue or not new_date:
                                    skipped_unknown_invalid_date += 1
                                    skipped_unknown_events += 1
                                    continue

                                new_stage_values = []
                                for col_name in imported_stage_cols:
                                    raw_val = csv_row.get(col_name, '')
                                    val = '' if pd.isna(raw_val) else str(raw_val).strip()
                                    if not val:
                                        continue
                                    spec = column_to_spec.get(col_name)
                                    if not spec:
                                        continue
                                    new_stage_values.append({
                                        'col': col_name,
                                        'deUID': spec['deUID'],
                                        'deName': spec['deName'],
                                        'deType': spec.get('deType', ''),
                                        'value': val,
                                    })

                                if not new_stage_values:
                                    skipped_unknown_missing_values += 1
                                    skipped_unknown_events += 1
                                    continue

                                import_row_id = f"__IMPORTED__{len(imported_create_changes) + 1}"
                                imported_create_changes.append({
                                    'templateRowId': import_row_id,
                                    'eventId': '',
                                    'personId': person_id_csv,
                                    'personName': str(csv_row.get(name_col, '') or '').strip() if name_col else '',
                                    'eventDate': new_date,
                                    'templateField': 'Person ID',
                                    'deUID': '__CREATE_PERSON_ID__',
                                    'deName': 'Person ID',
                                    'deType': 'TEXT',
                                    'oldValue': '',
                                    'newValue': person_id_csv,
                                    'changeType': 'CREATE_PERSON_ID',
                                    'operation': 'CREATE',
                                    'eventOnly': True,
                                })
                                imported_create_changes.append({
                                    'templateRowId': import_row_id,
                                    'eventId': '',
                                    'personId': person_id_csv,
                                    'personName': str(csv_row.get(name_col, '') or '').strip() if name_col else '',
                                    'eventDate': new_date,
                                    'templateField': 'Event Date',
                                    'deUID': '__CREATE_EVENT_DATE__',
                                    'deName': 'Event Date',
                                    'deType': 'DATE',
                                    'oldValue': '',
                                    'newValue': new_date,
                                    'changeType': 'CREATE_EVENT_DATE',
                                    'operation': 'CREATE',
                                    'eventOnly': True,
                                })
                                for sv in new_stage_values:
                                    imported_create_changes.append({
                                        'templateRowId': import_row_id,
                                        'eventId': '',
                                        'personId': person_id_csv,
                                        'personName': str(csv_row.get(name_col, '') or '').strip() if name_col else '',
                                        'eventDate': new_date,
                                        'templateField': sv['col'],
                                        'deUID': sv['deUID'],
                                        'deName': sv['deName'],
                                        'deType': sv['deType'],
                                        'oldValue': '',
                                        'newValue': sv['value'],
                                        'changeType': 'CREATE_DATA_VALUE',
                                        'operation': 'CREATE',
                                        'eventOnly': True,
                                    })
                                continue

                            original_for_event = original_values.get(event_id, {})
                            for col_name in imported_stage_cols:
                                raw_val = csv_row.get(col_name, '')
                                val = '' if pd.isna(raw_val) else str(raw_val).strip()
                                spec = column_to_spec.get(col_name)
                                if not spec:
                                    continue
                                old_val = str(original_for_event.get(spec['deUID'], '') or '').strip()
                                if val != old_val:
                                    if row_id not in new_overrides:
                                        new_overrides[row_id] = {}
                                    new_overrides[row_id][col_name] = val

                            if has_event_date_col:
                                raw_date = csv_row.get('Event Date', '')
                                new_date_raw = '' if pd.isna(raw_date) else str(raw_date).strip()
                                old_date_raw = st.session_state.event_meta.get(event_id, {}).get('eventDate', '')
                                old_date, _ = _normalize_event_date(old_date_raw)
                                new_date, parse_issue = _normalize_event_date(new_date_raw)
                                if parse_issue:
                                    invalid_event_date_rows += 1
                                    continue
                                if new_date != old_date:
                                    if row_id not in new_overrides:
                                        new_overrides[row_id] = {}
                                    new_overrides[row_id]['Event Date'] = new_date

                            if has_enrollment_date_col:
                                raw_enrollment_date = csv_row.get('Enrollment Date', '')
                                new_enrollment_date_raw = '' if pd.isna(raw_enrollment_date) else str(raw_enrollment_date).strip()
                                old_enrollment_date_raw = st.session_state.event_meta.get(event_id, {}).get('enrollmentDate', '')
                                old_enrollment_date, _ = _normalize_event_date(old_enrollment_date_raw)
                                new_enrollment_date, parse_issue = _normalize_event_date(new_enrollment_date_raw)
                                if parse_issue:
                                    invalid_enrollment_date_rows += 1
                                    continue
                                if new_enrollment_date != old_enrollment_date:
                                    if row_id not in new_overrides:
                                        new_overrides[row_id] = {}
                                    new_overrides[row_id]['Enrollment Date'] = new_enrollment_date

                        st.session_state.event_template_overrides = new_overrides
                        st.session_state.event_import_create_changes = imported_create_changes
                        st.session_state.event_show_review = False
                        st.session_state.events_editor_rev += 1
                        if new_overrides:
                            changed_cells = sum(len(v) for v in new_overrides.values())
                            imported_event_create_rows = len({c.get('templateRowId', '') for c in imported_create_changes})
                            st.success(
                                f"Loaded template values for {len(new_overrides)} event(s), {changed_cells} changed field(s)."
                                + (f" Skipped {skipped_unknown_events} unknown event row(s)." if skipped_unknown_events else "")
                            )
                            if imported_event_create_rows:
                                st.info(
                                    f"Prepared {imported_event_create_rows} unknown event row(s) as event-only creates for existing Person IDs."
                                )
                            if skipped_create_rows_multi:
                                st.warning(
                                    f"Skipped {skipped_create_rows_multi} __NEW__ row(s) because multi-school mode supports updates only."
                                )
                            if skipped_unknown_missing_person:
                                st.warning(
                                    f"Skipped {skipped_unknown_missing_person} unknown row(s) without Person ID."
                                )
                            if skipped_unknown_invalid_date:
                                st.warning(
                                    f"Skipped {skipped_unknown_invalid_date} unknown row(s) with invalid Event Date."
                                )
                            if skipped_unknown_missing_values:
                                st.warning(
                                    f"Skipped {skipped_unknown_missing_values} unknown row(s) with no stage values."
                                )
                            if invalid_event_date_rows:
                                st.warning(
                                    f"Skipped {invalid_event_date_rows} row(s) with invalid Event Date format. "
                                    "Use YYYY-MM-DD or ISO datetime."
                                )
                            if invalid_enrollment_date_rows:
                                st.warning(
                                    f"Skipped {invalid_enrollment_date_rows} row(s) with invalid Enrollment Date format. "
                                    "Use YYYY-MM-DD or ISO datetime."
                                )
                            st.rerun()
                        else:
                            imported_event_create_rows = len({c.get('templateRowId', '') for c in imported_create_changes})
                            if imported_event_create_rows:
                                st.info(
                                    f"Prepared {imported_event_create_rows} unknown event row(s) as event-only creates for existing Person IDs."
                                )
                                st.rerun()
                            elif non_stage_changes > 0:
                                st.warning(
                                    "No postable stage-field changes detected. It looks like only identification/attribute columns "
                                    "were edited, and those are not pushed in Event update payloads."
                                )
                            elif invalid_event_date_rows:
                                st.warning(
                                    "No changes were loaded because Event Date format was invalid. "
                                    "Use YYYY-MM-DD or ISO datetime."
                                )
                            elif invalid_enrollment_date_rows:
                                st.warning(
                                    "No changes were loaded because Enrollment Date format was invalid. "
                                    "Use YYYY-MM-DD or ISO datetime."
                                )
                            else:
                                st.info("No changes detected from uploaded template.")
            except Exception as e:
                st.error(f"Failed to process uploaded Events CSV: {e}")
    with tcol3:
        if events_multi_scope:
            st.info(
                "Download and re-upload events for the selected schools. Existing event updates are supported across schools. "
                "New __NEW__ row creation is disabled in multi-school mode."
            )
        else:
            st.info(
                "Download the school events template, edit existing rows to update records, or fill the blank __NEW__ rows "
                "to create new staff, enrollment, and first event for this school."
            )

    st.markdown("---")
    editor_key = f"events_editor_{st.session_state.get('events_editor_rev', 0)}"
    disabled_cols = ['Template Row ID', 'Event ID', 'Org Unit UID', 'Org Unit', 'Person ID']
    total_rows = len(df)
    p1, p2, p3 = st.columns([1, 1, 3])
    with p1:
        page_size = st.selectbox(
            "Rows per page",
            options=[100, 250, 500, 1000],
            index=1,
            key="events_page_size",
        )
    max_page = max(1, (total_rows + page_size - 1) // page_size)
    with p2:
        current_page = int(st.number_input(
            "Page",
            min_value=1,
            max_value=max_page,
            value=min(int(st.session_state.get('events_page', 1) or 1), max_page),
            step=1,
            key="events_page",
        ))
    start_row = (current_page - 1) * page_size
    end_row = min(start_row + page_size, total_rows)
    st.caption(f"Showing rows {start_row + 1}-{end_row} of {total_rows}")

    page_df = df.iloc[start_row:end_row].copy()
    edited_page_df = st.data_editor(
        page_df,
        key=editor_key,
        disabled=disabled_cols,
        column_config={
            'Status': st.column_config.SelectboxColumn(
                'Status',
                options=['ACTIVE', 'COMPLETED'],
                required=False,
            )
        },
        use_container_width=True,
        hide_index=True,
        num_rows='fixed'
    )

    # Merge page edits back onto the full table so fallback review logic can still see all rows.
    edited_df = df.copy()
    edited_df.loc[edited_page_df.index, edited_page_df.columns] = edited_page_df

    changes = []
    original_values = st.session_state.event_original_values
    event_attr_cols = st.session_state.get('event_attr_columns', [])
    attr_specs = st.session_state.get('event_attr_specs', [])
    name_col = next((c for c in event_attr_cols if 'name' in c.lower()), '')
    existing_row_ctx = {}
    for row_idx, row in edited_page_df.iterrows():
        row_id = str(row.get('Template Row ID', '') or '').strip()
        event_id = row.get('Event ID', '')
        if not row_id:
            continue
        person_id_raw = row.get('Person ID', '')
        person_id = '' if (pd.isna(person_id_raw) if not isinstance(person_id_raw, str) else person_id_raw.strip() in ('', 'nan')) else str(person_id_raw).strip()
        if person_id == 'nan':
            person_id = ''
        person_name_raw = row.get(name_col, '') if name_col else ''
        person_name = '' if (pd.isna(person_name_raw) if not isinstance(person_name_raw, str) else False) else str(person_name_raw or '').strip()
        is_create_row = row_id.startswith('__NEW__')

        if is_create_row and events_multi_scope:
            continue

        if is_create_row:
            if not _row_has_create_values(row, attr_specs, specs):
                continue

            if person_id:
                changes.append({
                    'templateRowId': row_id,
                    'eventId': '',
                    'personId': person_id,
                    'personName': person_name,
                    'eventDate': '',
                    'templateField': 'Person ID',
                    'deUID': '__CREATE_PERSON_ID__',
                    'deName': 'Person ID',
                    'deType': 'TEXT',
                    'oldValue': '',
                    'newValue': person_id,
                    'changeType': 'CREATE_PERSON_ID',
                    'operation': 'CREATE',
                })

            new_event_date_raw = row.get('Event Date', '')
            new_event_date = '' if pd.isna(new_event_date_raw) else str(new_event_date_raw).strip()
            new_event_date_norm, _ = _normalize_event_date(new_event_date)
            if new_event_date_norm:
                changes.append({
                    'templateRowId': row_id,
                    'eventId': '',
                    'personId': person_id,
                    'personName': person_name,
                    'eventDate': new_event_date_norm,
                    'templateField': 'Event Date',
                    'deUID': '__CREATE_EVENT_DATE__',
                    'deName': 'Event Date',
                    'deType': 'DATE',
                    'oldValue': '',
                    'newValue': new_event_date_norm,
                    'changeType': 'CREATE_EVENT_DATE',
                    'operation': 'CREATE',
                })

            new_enrollment_date_raw = row.get('Enrollment Date', '')
            new_enrollment_date = '' if pd.isna(new_enrollment_date_raw) else str(new_enrollment_date_raw).strip()
            new_enrollment_date_norm, _ = _normalize_event_date(new_enrollment_date)
            if new_enrollment_date_norm:
                changes.append({
                    'templateRowId': row_id,
                    'eventId': '',
                    'personId': person_id,
                    'personName': person_name,
                    'eventDate': new_event_date_norm,
                    'templateField': 'Enrollment Date',
                    'deUID': '__CREATE_ENROLLMENT_DATE__',
                    'deName': 'Enrollment Date',
                    'deType': 'DATE',
                    'oldValue': '',
                    'newValue': new_enrollment_date_norm,
                    'changeType': 'CREATE_ENROLLMENT_DATE',
                    'operation': 'CREATE',
                })

            for attr_spec in attr_specs:
                new_raw = row.get(attr_spec['column'], '')
                new_val = '' if pd.isna(new_raw) else str(new_raw).strip()
                if new_val:
                    changes.append({
                        'templateRowId': row_id,
                        'eventId': '',
                        'personId': person_id,
                        'personName': person_name,
                        'eventDate': new_event_date_norm,
                        'templateField': attr_spec['column'],
                        'attrUID': attr_spec['attrUID'],
                        'deUID': attr_spec['attrUID'],
                        'deName': attr_spec['attrName'],
                        'deType': attr_spec.get('attrType', ''),
                        'oldValue': '',
                        'newValue': new_val,
                        'changeType': 'CREATE_ATTRIBUTE',
                        'operation': 'CREATE',
                    })

            for col_name, spec in column_to_spec.items():
                new_raw = row.get(col_name, '')
                new_val = '' if pd.isna(new_raw) else str(new_raw).strip()
                if new_val:
                    changes.append({
                        'templateRowId': row_id,
                        'eventId': '',
                        'personId': person_id,
                        'personName': person_name,
                        'eventDate': new_event_date_norm,
                        'templateField': col_name,
                        'deUID': spec['deUID'],
                        'deName': spec['deName'],
                        'deType': spec.get('deType', ''),
                        'oldValue': '',
                        'newValue': new_val,
                        'changeType': 'CREATE_DATA_VALUE',
                        'operation': 'CREATE',
                    })
            continue

        if not event_id:
            continue
        original_for_event = original_values.get(event_id, {})
        enrollment_id = str(st.session_state.event_meta.get(event_id, {}).get('enrollment', '') or '')

        old_event_date_raw = st.session_state.event_meta.get(event_id, {}).get('eventDate', '')
        old_event_date, _ = _normalize_event_date(old_event_date_raw)
        new_event_date_raw = row.get('Event Date', '')
        new_event_date = '' if pd.isna(new_event_date_raw) else str(new_event_date_raw).strip()
        new_event_date_norm, _ = _normalize_event_date(new_event_date)
        if new_event_date_norm != old_event_date:
            changes.append({
                'eventId': event_id,
                'templateRowId': row_id,
                'personId': person_id,
                'personName': person_name,
                'enrollmentId': enrollment_id,
                'eventDate': new_event_date_norm,
                'templateField': 'Event Date',
                'deUID': '__EVENT_DATE__',
                'deName': 'Event Date',
                'deType': 'DATE',
                'oldValue': old_event_date,
                'newValue': new_event_date_norm,
                'changeType': 'EVENT_DATE',
                'operation': 'UPDATE',
            })

        old_enrollment_date_raw = st.session_state.event_meta.get(event_id, {}).get('enrollmentDate', '')
        old_enrollment_date, _ = _normalize_event_date(old_enrollment_date_raw)
        new_enrollment_date_raw = row.get('Enrollment Date', '')
        new_enrollment_date = '' if pd.isna(new_enrollment_date_raw) else str(new_enrollment_date_raw).strip()
        new_enrollment_date_norm, _ = _normalize_event_date(new_enrollment_date)
        if new_enrollment_date_norm != old_enrollment_date:
            changes.append({
                'eventId': event_id,
                'personId': person_id,
                'personName': person_name,
                'enrollmentId': enrollment_id,
                'eventDate': row.get('Event Date', ''),
                'templateField': 'Enrollment Date',
                'deUID': '__ENROLLMENT_DATE__',
                'deName': 'Enrollment Date',
                'deType': 'DATE',
                'oldValue': old_enrollment_date,
                'newValue': new_enrollment_date_norm,
                'changeType': 'ENROLLMENT_DATE',
                'operation': 'UPDATE',
            })

        old_status = str(st.session_state.event_meta.get(event_id, {}).get('status', 'ACTIVE') or 'ACTIVE').upper()
        new_status_raw = row.get('Status', '')
        new_status = '' if pd.isna(new_status_raw) else str(new_status_raw).strip().upper()
        if new_status in ('ACTIVE', 'COMPLETED') and new_status != old_status:
            changes.append({
                'eventId': event_id,
                'templateRowId': row_id,
                'personId': person_id,
                'personName': person_name,
                'enrollmentId': enrollment_id,
                'eventDate': row.get('Event Date', ''),
                'templateField': 'Status',
                'deUID': '__EVENT_STATUS__',
                'deName': 'Status',
                'deType': 'TEXT',
                'oldValue': old_status,
                'newValue': new_status,
                'changeType': 'EVENT_STATUS',
                'operation': 'UPDATE',
            })
        existing_row_ctx[row_idx] = {
            'eventId': event_id,
            'templateRowId': row_id,
            'personId': person_id,
            'personName': person_name,
            'enrollmentId': enrollment_id,
            'eventDate': row.get('Event Date', ''),
        }

    # Vectorized data value diff detection for existing rows on the current page.
    if existing_row_ctx:
        existing_idx = list(existing_row_ctx.keys())
        existing_df = edited_page_df.loc[existing_idx].copy()
        existing_df['__eventId'] = existing_df['Event ID'].astype(str).str.strip()
        for col_name, spec in column_to_spec.items():
            de_uid = spec['deUID']
            de_type = spec.get('deType', '')
            new_series = existing_df[col_name].map(lambda v: _normalize_event_compare_value(v, de_type))
            old_series = existing_df['__eventId'].map(
                lambda eid: _normalize_event_compare_value(original_values.get(eid, {}).get(de_uid, ''), de_type)
            )
            diff_mask = new_series != old_series
            if not bool(diff_mask.any()):
                continue
            for idx in existing_df.index[diff_mask]:
                ctx = existing_row_ctx.get(idx, {})
                if not ctx.get('eventId'):
                    continue
                changes.append({
                    'eventId': ctx['eventId'],
                    'templateRowId': ctx['templateRowId'],
                    'personId': ctx['personId'],
                    'personName': ctx['personName'],
                    'enrollmentId': ctx['enrollmentId'],
                    'eventDate': ctx['eventDate'],
                    'templateField': col_name,
                    'deUID': de_uid,
                    'deName': spec['deName'],
                    'deType': de_type,
                    'oldValue': old_series.loc[idx],
                    'newValue': new_series.loc[idx],
                    'changeType': 'DATA_VALUE',
                    'operation': 'UPDATE',
                })

    # Fallback: when changes came from uploaded template overrides, make sure they are still detected.
    if not changes and st.session_state.get('event_template_overrides'):
        event_row_map = {
            str(r.get('Template Row ID', '') or ''): r
            for _, r in edited_df.iterrows()
        }
        for row_id, row_overrides in st.session_state['event_template_overrides'].items():
            row_ctx = event_row_map.get(row_id)
            event_id = str(row_ctx.get('Event ID', '') or '') if row_ctx is not None else ''
            is_create_row = str(row_id).startswith('__NEW__')
            if is_create_row and events_multi_scope:
                continue
            original_for_event = original_values.get(event_id, {})
            person_id = str(row_ctx.get('Person ID', '') or '') if row_ctx is not None else ''
            person_name = str(row_ctx.get(name_col, '') or '') if (row_ctx is not None and name_col) else ''
            event_date = str(row_ctx.get('Event Date', '') or '') if row_ctx is not None else ''
            enrollment_id = str(st.session_state.event_meta.get(event_id, {}).get('enrollment', '') or '')
            for col_name, val in row_overrides.items():
                if is_create_row:
                    if col_name == 'Person ID':
                        person_val = str(val or '').strip()
                        if person_val:
                            changes.append({
                                'templateRowId': row_id,
                                'eventId': '',
                                'personId': person_val,
                                'personName': person_name,
                                'eventDate': event_date,
                                'templateField': 'Person ID',
                                'deUID': '__CREATE_PERSON_ID__',
                                'deName': 'Person ID',
                                'deType': 'TEXT',
                                'oldValue': '',
                                'newValue': person_val,
                                'changeType': 'CREATE_PERSON_ID',
                                'operation': 'CREATE',
                            })
                        continue
                    if col_name == 'Event Date':
                        new_event_date_norm, _ = _normalize_event_date(str(val or '').strip())
                        if new_event_date_norm:
                            changes.append({
                                'templateRowId': row_id,
                                'eventId': '',
                                'personId': person_id,
                                'personName': person_name,
                                'eventDate': new_event_date_norm,
                                'templateField': 'Event Date',
                                'deUID': '__CREATE_EVENT_DATE__',
                                'deName': 'Event Date',
                                'deType': 'DATE',
                                'oldValue': '',
                                'newValue': new_event_date_norm,
                                'changeType': 'CREATE_EVENT_DATE',
                                'operation': 'CREATE',
                            })
                    elif col_name == 'Enrollment Date':
                        new_enrollment_date_norm, _ = _normalize_event_date(str(val or '').strip())
                        if new_enrollment_date_norm:
                            changes.append({
                                'templateRowId': row_id,
                                'eventId': '',
                                'personId': person_id,
                                'personName': person_name,
                                'eventDate': event_date,
                                'templateField': 'Enrollment Date',
                                'deUID': '__CREATE_ENROLLMENT_DATE__',
                                'deName': 'Enrollment Date',
                                'deType': 'DATE',
                                'oldValue': '',
                                'newValue': new_enrollment_date_norm,
                                'changeType': 'CREATE_ENROLLMENT_DATE',
                                'operation': 'CREATE',
                            })
                    elif col_name in {spec['column'] for spec in attr_specs}:
                        attr_spec = next((spec for spec in attr_specs if spec['column'] == col_name), None)
                        if attr_spec and str(val or '').strip():
                            changes.append({
                                'templateRowId': row_id,
                                'eventId': '',
                                'personId': person_id,
                                'personName': person_name,
                                'eventDate': event_date,
                                'templateField': col_name,
                                'attrUID': attr_spec['attrUID'],
                                'deUID': attr_spec['attrUID'],
                                'deName': attr_spec['attrName'],
                                'deType': attr_spec.get('attrType', ''),
                                'oldValue': '',
                                'newValue': str(val or '').strip(),
                                'changeType': 'CREATE_ATTRIBUTE',
                                'operation': 'CREATE',
                            })
                    else:
                        spec = column_to_spec.get(col_name)
                        if spec and str(val or '').strip():
                            changes.append({
                                'templateRowId': row_id,
                                'eventId': '',
                                'personId': person_id,
                                'personName': person_name,
                                'eventDate': event_date,
                                'templateField': col_name,
                                'deUID': spec['deUID'],
                                'deName': spec['deName'],
                                'deType': spec.get('deType', ''),
                                'oldValue': '',
                                'newValue': str(val or '').strip(),
                                'changeType': 'CREATE_DATA_VALUE',
                                'operation': 'CREATE',
                            })
                    continue
                if col_name == 'Event Date':
                    old_event_date_raw = st.session_state.event_meta.get(event_id, {}).get('eventDate', '')
                    old_event_date, _ = _normalize_event_date(old_event_date_raw)
                    new_event_date_raw = str(val or '').strip()
                    new_event_date_norm, _ = _normalize_event_date(new_event_date_raw)
                    if new_event_date_norm != old_event_date:
                        changes.append({
                            'templateRowId': row_id,
                            'eventId': event_id,
                            'personId': person_id,
                            'personName': person_name,
                            'enrollmentId': enrollment_id,
                            'eventDate': new_event_date_norm,
                            'templateField': 'Event Date',
                            'deUID': '__EVENT_DATE__',
                            'deName': 'Event Date',
                            'deType': 'DATE',
                            'oldValue': old_event_date,
                            'newValue': new_event_date_norm,
                            'changeType': 'EVENT_DATE',
                            'operation': 'UPDATE',
                        })
                    continue
                if col_name == 'Enrollment Date':
                    old_enrollment_date_raw = st.session_state.event_meta.get(event_id, {}).get('enrollmentDate', '')
                    old_enrollment_date, _ = _normalize_event_date(old_enrollment_date_raw)
                    new_enrollment_date_raw = str(val or '').strip()
                    new_enrollment_date_norm, _ = _normalize_event_date(new_enrollment_date_raw)
                    if new_enrollment_date_norm != old_enrollment_date:
                        changes.append({
                            'templateRowId': row_id,
                            'eventId': event_id,
                            'personId': person_id,
                            'personName': person_name,
                            'enrollmentId': enrollment_id,
                            'eventDate': event_date,
                            'templateField': 'Enrollment Date',
                            'deUID': '__ENROLLMENT_DATE__',
                            'deName': 'Enrollment Date',
                            'deType': 'DATE',
                            'oldValue': old_enrollment_date,
                            'newValue': new_enrollment_date_norm,
                            'changeType': 'ENROLLMENT_DATE',
                            'operation': 'UPDATE',
                        })
                    continue
                spec = column_to_spec.get(col_name)
                if not spec:
                    continue
                de_type = spec.get('deType', '')
                new_val = _normalize_event_compare_value(val, de_type)
                old_val = _normalize_event_compare_value(original_for_event.get(spec['deUID'], ''), de_type)
                if new_val != old_val:
                    changes.append({
                        'templateRowId': row_id,
                        'eventId': event_id,
                        'personId': person_id,
                        'personName': person_name,
                        'enrollmentId': enrollment_id,
                        'eventDate': event_date,
                        'templateField': col_name,
                        'deUID': spec['deUID'],
                        'deName': spec['deName'],
                        'deType': spec.get('deType', ''),
                        'oldValue': old_val,
                        'newValue': new_val,
                        'changeType': 'DATA_VALUE',
                        'operation': 'UPDATE',
                    })

    # Include event-only create changes prepared from imported unknown Event IDs.
    imported_create_changes = st.session_state.get('event_import_create_changes', []) or []
    if imported_create_changes:
        changes.extend(imported_create_changes)

    # Deduplicate: same (eventId, deUID) from both paths ÔåÆ keep last entry
    seen_keys = {}
    for ch in changes:
        dedupe_event = ch.get('eventId', '') or ch.get('templateRowId', '')
        seen_keys[(dedupe_event, ch['deUID'])] = ch
    changes = list(seen_keys.values())

    st.session_state.event_has_unsaved_edits = len(changes) > 0

    if st.session_state.get('event_show_review'):
        st.markdown("---")
        st.subheader("­ƒôï Review Event Changes Before Pushing")
        visible_changes = [c for c in changes if c.get('changeType') != 'CREATE_PERSON_ID']
        st.info(f"**{len(visible_changes)} field value(s)** will be updated in DHIS2 events.")
        if changes:
            review_changes = [c for c in changes if c.get('changeType') != 'CREATE_PERSON_ID']
            review_df = pd.DataFrame(review_changes)
            review_cols = [
                'operation', 'personName', 'personId', 'eventId', 'eventDate',
                'templateField', 'oldValue', 'newValue', 'deType'
            ]
            for c in review_cols:
                if c not in review_df.columns:
                    review_df[c] = ''
            review_df = review_df[review_cols].rename(columns={
                'operation': 'Operation',
                'personName': 'Person',
                'personId': 'Person ID',
                'eventId': 'Event ID',
                'eventDate': 'Event Date',
                'templateField': 'Template Field',
                'oldValue': 'Current Value',
                'newValue': 'New Value',
                'deType': 'Type',
            })
            st.dataframe(review_df, use_container_width=True, hide_index=True)
        else:
            st.info("No event changes detected.")

        r1, r2 = st.columns([1, 4])
        with r1:
            if st.button("Ô£à Confirm Event Push", type="primary"):
                ok = push_events_to_dhis2(changes)
                if ok:
                    with st.spinner("Refreshing events..."):
                        load_events_data()
                        st.session_state['events_editor_rev'] += 1
                        st.session_state['event_show_review'] = False
        with r2:
            if st.button("ÔåÉ Cancel", key="cancel_event_review"):
                st.session_state['event_show_review'] = False
                st.rerun()
    else:
        if changes:
            st.markdown("---")
            if st.button(f"Review & Push {len(changes)} Event Change(s)", type="primary"):
                st.session_state['event_show_review'] = True
                st.rerun()


_POSITIVE_INT_TYPES = {'INTEGER_POSITIVE', 'INTEGER_ZERO_OR_POSITIVE'}
_NUMERIC_TYPES = {'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE',
                  'INTEGER_ZERO_OR_POSITIVE', 'NUMBER', 'UNIT_INTERVAL', 'PERCENTAGE'}
_BOOL_TYPES = {'BOOLEAN', 'TRUE_ONLY'}
_CSV_SAVE_CHUNK_SIZE = 250
_AGGREGATE_PUSH_CHUNK_SIZE = 50


def _iter_chunks(values, chunk_size):
    """Yield chunk slices from a list-like sequence."""
    size = max(1, int(chunk_size or 1))
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def _normalize_aggregate_date(value):
    """Normalize aggregate date input to YYYY-MM-DD.

    Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY and ISO datetime values.
    """
    text = str(value or '').strip()
    if not text:
        return '', 'Date value is empty'

    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return text, None

    if re.fullmatch(r'\d{2}[/-]\d{2}[/-]\d{4}', text):
        fmt = '%d/%m/%Y' if '/' in text else '%d-%m-%Y'
        try:
            return datetime.strptime(text, fmt).date().isoformat(), None
        except ValueError:
            return '', f"Expected a valid date (got '{text}')"

    try:
        iso_text = text.replace('Z', '+00:00')
        return datetime.fromisoformat(iso_text).date().isoformat(), None
    except ValueError:
        return '', f"Expected date in YYYY-MM-DD or DD/MM/YYYY format (got '{text}')"


def _normalize_aggregate_datetime(value):
    """Normalize aggregate datetime input to YYYY-MM-DDTHH:MM:SS."""
    text = str(value or '').strip()
    if not text:
        return '', 'Datetime value is empty'

    # Accept ISO-like values and plain date values.
    iso_candidate = text.replace(' ', 'T').replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return parsed.strftime('%Y-%m-%dT%H:%M:%S'), None
    except ValueError:
        pass

    # Accept DD/MM/YYYY[ HH:MM[:SS]] and DD-MM-YYYY[ HH:MM[:SS]].
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
                '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y'):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime('%Y-%m-%dT%H:%M:%S'), None
        except ValueError:
            continue

    return '', f"Expected datetime in ISO, DD/MM/YYYY HH:MM[:SS], or DD-MM-YYYY HH:MM[:SS] format (got '{text}')"


def _validate_push_entries(entries, rows):
    """
    Run data-quality checks on entries before pushing.
    Returns (clean_entries, issues) where issues is a list of dicts.
    """
    row_meta = {r['row_key']: r for r in rows}
    clean, issues = [], []

    for entry in entries:
        key = str(entry.get('row_key', '') or '')
        if not key:
            key = f"{entry.get('orgUnitUID', st.session_state.org_unit_uid)}|{entry['deUID']}|{entry['cocUID']}"
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
                    issue = f"Must be ÔëÑ 0 (got {val})"
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
        elif de_type == 'DATE':
            normalized_date, parse_issue = _normalize_aggregate_date(val)
            if parse_issue:
                issue = parse_issue
            else:
                entry['value'] = normalized_date
                val = normalized_date
        elif de_type == 'DATETIME':
            normalized_datetime, parse_issue = _normalize_aggregate_datetime(val)
            if parse_issue:
                issue = parse_issue
            else:
                entry['value'] = normalized_datetime
                val = normalized_datetime

        if issue:
            issues.append({'Data Element': name, 'Value': val, 'Type': de_type, 'Issue': issue})
        else:
            clean.append(entry)

    return clean, issues


def push_to_dhis2():
    """Push edited values to DHIS2"""
    st.session_state['aggregate_post_feedback'] = None
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
                    'orgUnitUID': row.get('orgUnitUID', st.session_state.org_unit_uid),
                    'row_key': row['row_key'],
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
        st.warning(f"ÔÜá´©Å {len(dq_issues)} value(s) failed quality checks and were removed from this push:")
        st.dataframe(pd.DataFrame(dq_issues), use_container_width=True, hide_index=True)
    if not entries:
        st.error("All values failed validation ÔÇö nothing was sent to DHIS2.")
        return

    try:
        by_ou = {}
        for e in entries:
            ou_uid = str(e.get('orgUnitUID', '') or st.session_state.org_unit_uid)
            by_ou.setdefault(ou_uid, []).append(e)

        progress = st.progress(0, text="Preparing to push reviewed changes to DHIS2...")
        status_placeholder = st.empty()

        sent_entry_map = {
            str(e.get('row_key', '') or f"{e.get('orgUnitUID', st.session_state.org_unit_uid)}|{e['deUID']}|{e['cocUID']}"): e
            for e in entries
        }

        imported = updated = ignored = 0
        status = 'SUCCESS'
        message_parts = []
        all_conflicts = []
        total_ous = max(len(by_ou), 1)
        total_chunks = sum(
            max(1, (len(ou_entries) + _AGGREGATE_PUSH_CHUNK_SIZE - 1) // _AGGREGATE_PUSH_CHUNK_SIZE)
            for ou_entries in by_ou.values()
        )
        done_chunks = 0

        with st.spinner("Pushing reviewed changes to DHIS2..."):
            for index, (ou_uid, ou_entries_full) in enumerate(by_ou.items(), start=1):
                ou_entries = [
                    {'deUID': e['deUID'], 'cocUID': e['cocUID'], 'value': e['value']}
                    for e in ou_entries_full
                ]
                db.save_local_values(
                    ou_uid,
                    str(st.session_state.selected_period),
                    ou_entries
                )
                ou_imported = ou_updated = ou_ignored = 0
                ou_status = 'SUCCESS'
                ou_message_parts = []
                conflicts = ''

                chunks = list(_iter_chunks(ou_entries, _AGGREGATE_PUSH_CHUNK_SIZE))
                for school_chunk_index, ou_chunk in enumerate(chunks, start=1):
                    in_flight_chunk = done_chunks + 1
                    progress.progress(
                        done_chunks / max(total_chunks, 1),
                        text=(
                            f"Sending chunk {in_flight_chunk} of {total_chunks} "
                            f"(school {index}/{total_ous}, {len(ou_chunk)} values)..."
                        )
                    )
                    status_placeholder.info(
                        f"Pushing school {index} of {total_ous} "
                        f"(chunk {school_chunk_index} of {len(chunks)}): "
                        f"{st.session_state.selected_org_unit_labels.get(ou_uid, ou_uid)}"
                    )
                    result = dhis2.push_data_values(
                        ou_uid,
                        str(st.session_state.selected_period),
                        st.session_state.selected_dataset,
                        ou_chunk,
                        st.session_state.username,
                        st.session_state.password
                    )
                    response_block = result.get('response', {}) if isinstance(result.get('response', {}), dict) else {}
                    imp = result.get('importSummary') or response_block.get('importSummary') or response_block or result
                    chunk_imported = int(imp.get('importCount', {}).get('imported', 0) or 0)
                    chunk_updated = int(imp.get('importCount', {}).get('updated', 0) or 0)
                    chunk_ignored = int(imp.get('importCount', {}).get('ignored', 0) or 0)
                    chunk_status = str(imp.get('status', 'UNKNOWN') or 'UNKNOWN')
                    chunk_message = imp.get('description', '') or imp.get('message', '') or ''
                    chunk_conflicts = str(imp.get('conflicts', '')) if imp.get('conflicts') else ''

                    imported += chunk_imported
                    updated += chunk_updated
                    ignored += chunk_ignored
                    ou_imported += chunk_imported
                    ou_updated += chunk_updated
                    ou_ignored += chunk_ignored

                    if chunk_conflicts:
                        conflicts = chunk_conflicts
                        all_conflicts.append(chunk_conflicts)
                    if chunk_status not in ('SUCCESS', 'OK'):
                        status = 'WARNING'
                        ou_status = 'WARNING'
                    if chunk_message:
                        message_parts.append(chunk_message)
                        ou_message_parts.append(chunk_message)

                    done_chunks += 1
                    progress.progress(
                        done_chunks / max(total_chunks, 1),
                        text=(
                            f"Pushed chunk {done_chunks} of {total_chunks} "
                            f"(school {index}/{total_ous})"
                        )
                    )

                db.log_sync(
                    ou_uid,
                    st.session_state.selected_dataset,
                    str(st.session_state.selected_period),
                    len(ou_entries),
                    ou_imported,
                    ou_updated,
                    ou_ignored,
                    ou_status,
                    '; '.join(ou_message_parts[:5]),
                    conflicts
                )

        status_placeholder.success(
            f"Push completed. Processed {total_chunks} chunk(s) across {total_ous} school(s)."
        )

        message = '; '.join(message_parts[:5])
        conflicts = '; '.join(all_conflicts[:5])
        if status in ('SUCCESS', 'OK'):
            st.session_state['aggregate_post_feedback'] = {
                'level': 'success',
                'title': f"Ô£à Successfully synced! Imported: {imported}, Updated: {updated}",
                'detail': message,
            }
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
            retry_keys = {
                str(e.get('row_key', '') or f"{e.get('orgUnitUID', st.session_state.org_unit_uid)}|{e['deUID']}|{e['cocUID']}")
                for e in retry
            }
            # Keep only values that were not accepted so the review button does not retain synced edits.
            st.session_state.edited_values = {
                k: v['value'] for k, v in sent_entry_map.items() if k in retry_keys
            }
            if not st.session_state.edited_values:
                st.session_state['section_edit_modes'] = {}
            st.session_state['aggregate_post_feedback'] = {
                'level': 'warning',
                'title': f"ÔÜá´©Å Sync completed with warnings: {status}",
                'detail': f"Imported: {imported}, Updated: {updated}, Ignored: {ignored}" + (f". {message}" if message else ''),
            }
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
        st.session_state['aggregate_post_feedback'] = {
            'level': 'error',
            'title': "Failed to push to DHIS2",
            'detail': str(e),
        }

# Main execution
init_session_state()

if not check_auth_expiry():
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    login_page()
else:
    main_app()
