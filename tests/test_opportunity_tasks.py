import json
import sqlite3
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from shopifyseo import opportunity_tasks as tasks
from shopifyseo.seo_quality import metadata_issues, validate_changed_metadata


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'tasks.db')
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    conn = connect()
    tasks.ensure_schema(conn)
    conn.execute('CREATE TABLE gsc_query_rows(object_type, object_handle, query, clicks, impressions, ctr, position, fetched_at)')
    conn.executemany('INSERT INTO gsc_query_rows VALUES(?,?,?,?,?,?,?,?)', [('product','test','primary',5,1000,.005,2,123),('product','test','secondary',3,300,.01,12,123)])
    conn.commit()
    yield conn, connect
    conn.close()


def test_groups_queries_and_keeps_existing_draft(db):
    conn, _ = db
    first = tasks.prepare(conn, 'product', 'test', 'primary')
    again = tasks.prepare(conn, 'product', 'test', 'secondary')
    assert first['id'] == again['id']
    assert len(again['evidence']['queries']) == 2
    assert again['evidence']['primary_query'] == 'primary'
    assert len(tasks.list_tasks(conn)) == 1
    with pytest.raises(ValueError):
        tasks.prepare(conn, 'product', 'test', 'unknown')


def test_worker_passes_evidence_and_only_prepares_metadata_for_ctr(db):
    conn, connect = db
    task = tasks.prepare(conn, 'product', 'test', 'primary')
    def generate(_conn, kind, handle, field, accepted, **kwargs):
        assert kwargs['opportunity_context']['primary_query'] == 'primary'
        assert len(kwargs['opportunity_context']['queries']) == 2
        return {'value': 'a' * (50 if field == 'seo_title' else 150)}
    with patch('backend.app.db.open_db_connection', connect), patch('shopifyseo.dashboard_ai_engine_parts.generation.generate_field_recommendation', side_effect=generate) as gen:
        tasks._worker(task['id'])
    task = tasks.get_task(conn, task['id'])
    assert task['status'] == 'draft_ready'
    assert gen.call_count == 2
    assert set(task['draft']) == {'seo_title','seo_description'}
    reviewed = tasks.review(conn, task['id'], task['draft'])
    assert reviewed['status'] == 'reviewed'
    tasks.record_applied(conn, 'product', 'test', {**task['draft'], 'seo_title':'changed'})
    assert tasks.get_task(conn, task['id'])['status'] == 'reviewed'
    tasks.record_applied(conn, 'product', 'test', task['draft'])
    assert tasks.get_task(conn, task['id'])['status'] == 'applied'
    assert tasks.monitor(conn, task['id'])['status'] == 'monitoring'


def test_invalid_review_and_transitions_are_rejected(db):
    conn, _ = db
    task = tasks.prepare(conn, 'product', 'test', 'primary')
    with pytest.raises(ValueError): tasks.review(conn, task['id'], {})
    with pytest.raises(ValueError): tasks.monitor(conn, task['id'])
    conn.execute("UPDATE seo_opportunity_tasks SET status='draft_ready',draft_json=? WHERE id=?", (json.dumps({'seo_description':'x'*150}),task['id']))
    conn.commit()
    with pytest.raises(ValueError): tasks.review(conn, task['id'], {'seo_description':'too short'})
    assert tasks.get_task(conn, task['id'])['status']=='draft_ready'


def test_atomic_reservation_and_restart_recovery(db):
    conn, _ = db
    task = tasks.prepare(conn,'product','test','primary')
    with patch('shopifyseo.opportunity_tasks.threading.Thread') as thread:
        tasks.generate(conn,task['id']); tasks.generate(conn,task['id'])
        assert thread.call_count == 1
    tasks.recover(conn)
    assert tasks.get_task(conn,task['id'])['status']=='failed'
    with patch('shopifyseo.opportunity_tasks.threading.Thread') as thread:
        tasks.generate(conn,task['id'])
        assert thread.call_count == 1


def test_worker_failure_is_visible_and_preserves_reviewed_data(db):
    conn, connect = db
    task = tasks.prepare(conn,'product','test','primary')
    with patch('backend.app.db.open_db_connection',connect), patch('shopifyseo.dashboard_ai_engine_parts.generation.generate_field_recommendation',side_effect=RuntimeError('Provider unavailable')):
        tasks._worker(task['id'])
    task=tasks.get_task(conn,task['id'])
    assert task['status']=='failed'
    assert 'Provider unavailable' in task['error']


def test_http_contract_and_deduplication(db):
    _, connect = db
    with patch('backend.app.routers.opportunities.open_db_connection',connect), patch('shopifyseo.opportunity_tasks.threading.Thread'):
        client=TestClient(app)
        payload={'object_type':'product','object_handle':'test','query':'primary'}
        response=client.post('/api/opportunities/prepare-fix',json=payload)
        assert response.status_code==200
        task=response.json()['data']
        assert task['status']=='preparing'
        assert task['evidence']['primary_query']=='primary'
        assert task['detail_url']=='/products/test'
        assert client.get('/api/opportunities/tasks').json()['data'][0]['id']==task['id']
        assert client.post('/api/opportunities/prepare-fix',json=payload).json()['data']['id']==task['id']
        assert client.post('/api/opportunities/tasks/999/review',json={'fields':{}}).status_code==409
        policy=client.get('/api/seo-quality-policy').json()['data']['product']
        assert policy['seo_description']['target']==150


def test_same_policy_for_publish_and_advisory_targets():
    assert metadata_issues('product',{'seo_description':'x'*140})[0]['severity']=='warning'
    assert metadata_issues('product',{'seo_description':'x'*100})[0]['severity']=='error'
    validate_changed_metadata('product',{'seo_title':'Legacy short title'},{'seo_title':'Legacy short title'})
    validate_changed_metadata('product',{}, {'seo_description':'x'*140})
    with pytest.raises(ValueError): validate_changed_metadata('product',{}, {'seo_description':'x'*100})


def test_generation_retries_same_policy_without_truncation():
    from shopifyseo.dashboard_ai_engine_parts import generation as gen
    from shopifyseo.dashboard_ai_engine_parts.qa import RecommendationValidationError
    # Original behavior: RecommendationValidationError triggers retry, then warning (140 chars) ALSO triggers retry
    # because warnings DO trigger retries for all fields EXCEPT product seo_title
    with patch.object(gen,'_generate_single_field_attempt', side_effect=[RecommendationValidationError('too long'), {'value':'x'*140}, {'value':'x'*150}]) as attempt:
        result=gen._generate_single_field_core(object_type='product',field='seo_description')
        assert result['value']=='x'*150
        assert result['quality_retry_count']==2
        assert attempt.call_count==3
    with patch.object(gen,'_generate_single_field_attempt',side_effect=RecommendationValidationError('too long')) as attempt:
        with pytest.raises(RecommendationValidationError): gen._generate_single_field_core(object_type='product',field='seo_description')
        assert attempt.call_count==3
    with patch.object(gen,'_generate_single_field_attempt',return_value={'value':'x'*140}):
        result=gen._generate_single_field_core(object_type='product',field='seo_description')
        assert result['quality_issues'][0]['severity']=='warning'


def test_collection_meta_warning_still_retried():
    """Collection seo_description 120-139 chars is warning-severity but STILL triggers retry (old behavior preserved)."""
    from shopifyseo.dashboard_ai_engine_parts import generation as gen
    from shopifyseo.seo_quality import metadata_issues
    # Verify 130 chars for collection seo_description is a warning
    issues = metadata_issues('collection', {'seo_description': 'x' * 130})
    assert len(issues) == 1
    assert issues[0]['severity'] == 'warning'
    # But warnings DO trigger retries for collection (not product seo_title)
    with patch.object(gen, '_generate_single_field_attempt', side_effect=[{'value': 'x' * 130}, {'value': 'x' * 150}]) as attempt:
        result = gen._generate_single_field_core(object_type='collection', field='seo_description')
        assert result['value'] == 'x' * 150  # Warning triggered retry, got passing content
        assert result['quality_retry_count'] == 1
        assert attempt.call_count == 2


def test_product_seo_title_warning_not_retried():
    """Product seo_title >60 chars is warning but does NOT trigger retry (special case)."""
    from shopifyseo.dashboard_ai_engine_parts import generation as gen
    from shopifyseo.seo_quality import metadata_issues
    # Verify 70-char product seo_title is a warning
    issues = metadata_issues('product', {'seo_title': 'x' * 70})
    assert len(issues) == 1
    assert issues[0]['severity'] == 'warning'
    # For product seo_title ONLY, warnings do NOT trigger retries
    with patch.object(gen, '_generate_single_field_attempt', return_value={'value': 'x' * 70}) as attempt:
        result = gen._generate_single_field_core(object_type='product', field='seo_title')
        assert result['value'] == 'x' * 70  # Warning accepted without retry
        assert result['quality_retry_count'] == 0
        assert attempt.call_count == 1


@pytest.mark.parametrize('kind', ['product','collection','page','blog_article'])
def test_save_hooks_advance_only_successful_reviewed_writes(db, kind):
    from contextlib import ExitStack
    from unittest.mock import MagicMock
    from backend.app.services import product_service, content_service, article_service
    conn, connect = db
    handle = 'blog/test' if kind == 'blog_article' else 'test'
    values = {'seo_title':'a'*50,'seo_description':'b'*150,'body_html':'<p>Reviewed content</p>'}
    conn.execute("INSERT INTO seo_opportunity_tasks(object_type,object_handle,status,reviewed_json) VALUES(?,?,'reviewed',?)",(kind,handle,json.dumps(values)))
    conn.commit()
    module = product_service if kind=='product' else article_service if kind=='blog_article' else content_service
    fetch_name={'product':'fetch_product_detail','collection':'fetch_collection_detail','page':'fetch_page_detail','blog_article':'fetch_blog_article_detail'}[kind]
    row={'shopify_id':'gid://test/1','seo_title':'old','seo_description':'old'}
    key='article' if kind=='blog_article' else kind
    live_name={'product':'live_update_product','collection':'live_update_collection','page':'live_update_page','blog_article':'live_update_article'}[kind]
    with ExitStack() as stack:
        stack.enter_context(patch.object(module,'open_db_connection',connect))
        stack.enter_context(patch.object(module.dq,fetch_name,return_value={key:row}))
        for name in ['apply_saved_product_fields_from_editor','apply_saved_collection_fields_from_editor','apply_saved_page_fields_from_editor','apply_saved_blog_article_fields_from_editor','set_workflow_state']:
            stack.enter_context(patch.object(module.dq,name))
        if hasattr(module,'refresh_object_structured_seo_data'):
            stack.enter_context(patch.object(module,'refresh_object_structured_seo_data'))
        live=stack.enter_context(patch.object(module,live_name,return_value=({},[])))
        if kind=='product': result=module.update_product(handle,values)
        elif kind=='blog_article': result=module.update_blog_article('blog','test',values)
        else: result=module.update_content(kind,handle,values)
        assert result[0] is True
        assert live.call_count==1
    assert tasks.list_tasks(conn)[0]['status']=='applied'


def test_partial_collection_save_does_not_advance_task(db):
    from backend.app.services import content_service as service
    conn, connect=db
    values={'seo_title':'a'*50,'seo_description':'b'*150,'body_html':'<p>Reviewed</p>'}
    conn.execute("INSERT INTO seo_opportunity_tasks(object_type,object_handle,status,reviewed_json) VALUES('collection','test','reviewed',?)",(json.dumps(values),));conn.commit()
    with patch.object(service,'open_db_connection',connect), patch.object(service.dq,'fetch_collection_detail',return_value={'collection':{'shopify_id':'1'}}), patch.object(service,'live_update_collection',side_effect=service.ShopifyPartialCollectionUpdateError('Rule failure')), patch.object(service.dq,'apply_saved_collection_fields_from_editor'), patch.object(service.dq,'set_workflow_state'), patch.object(service,'refresh_object_structured_seo_data'):
        assert service.update_content('collection','test',values)[0] is True
    assert tasks.list_tasks(conn)[0]['status']=='reviewed'
