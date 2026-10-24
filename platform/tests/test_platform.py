"""Real API flows on an isolated SQLite database."""
import sys
import sqlite3
import io
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from seed import seed
from manage import initialize

HEADERS = {'X-Requested-With': 'home-helper'}


def test_homepage_assets_and_presets(db_path):
    import re
    client = TestClient(create_app(db_path))
    page = client.get('/')
    assert page.status_code == 200
    names = re.findall(r'data-service-preset="([^"]+)"', page.text)
    catalog = client.get('/api/service-catalog').json()
    assert len(names) == 3 and set(names) <= {s['name'] for s in catalog}
    script = client.get('/assets/js/index.js').text
    assert client.get('/assets/js/support-safe.js').status_code == 200
    image_block = script.split('const serviceImages = {', 1)[1].split('};', 1)[0]
    images = dict(re.findall(r'"([^"]+)": "([^"]+)"', image_block))
    assert set(images) == {s['name'] for s in catalog}
    assert len(set(images.values())) == len(catalog)
    paths = re.findall(r'<img[^>]+src="([^"]+)"', page.text)
    paths += ['/assets/images/home/' + slug + '-v1.webp' for slug in images.values()]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers['content-type'] == 'image/webp'
        assert response.content[:4] == b'RIFF'
    assert client.get('/assets/images/missing.webp').status_code == 404
    assert client.get('/assets/images/%2e%2e/%2e%2e/index.html').status_code == 404
    for slide_number in range(1, 7):
        assert client.get(f'/slides/slide{slide_number}.png').status_code == 200
    assert 'aria-label="Войти в личный кабинет"' in page.text
    assert 'aria-hidden="true" inert' in page.text
    assert 'servicesTableBody' not in page.text
    assert 'role="tabpanel"' in page.text


def test_all_public_page_assets_are_available(db_path):
    import re
    client = TestClient(create_app(db_path))
    pages = ('/', '/application', '/account', '/master', '/masters', '/admin',
             '/about', '/support', '/referral', '/roadmap')
    for page_path in pages:
        response = client.get(page_path)
        assert response.status_code == 200, page_path
        assets = re.findall(r'(?:href|src)="(/?assets/(?:css|js|images)/[^"?#]+)', response.text)
        for asset in assets:
            asset_path = '/' + asset.lstrip('/')
            assert client.get(asset_path).status_code == 200, (page_path, asset_path)


@pytest.fixture
def db_path():
    # A file inside an existing directory also works in restricted Windows sandboxes,
    # where pytest's newly created tmp_path directory can receive inaccessible ACLs.
    base = Path(os.environ.get('HH_TEST_DIR', tempfile.gettempdir()))
    path = base / f'home-helper-test-{uuid4().hex}.sqlite3'
    return path


def setup(path):
    seed(path)
    return create_app(path, True), path


def login(app, email):
    client = TestClient(app)
    response = client.post('/api/auth/login', headers=HEADERS, json={'email': email, 'password': 'DemoPass2026!'})
    assert response.status_code == 200, response.text
    return client


def slot(client):
    for offset in range(2, 20):
        day = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=offset)).date().isoformat()
        response = client.get('/api/services/1/slots', params={'date': day})
        assert response.status_code == 200, response.text
        if response.json():
            return response.json()[0]
    raise AssertionError('Demo master has no free slot')


def order(client, key):
    return client.post('/api/orders', headers=HEADERS, json={
        'service_id': 1, 'address': 'Москва, Тверская, дом 7',
        'description': 'Пожалуйста, замените смеситель на кухне.',
        'scheduled_at': slot(client), 'request_key': key,
    })


def action(client, oid, name, reason=''):
    return client.post(f'/api/orders/{oid}/action', headers=HEADERS, json={'action': name, 'reason': reason})


def test_original_catalog_request_assignment_flow(db_path):
    app, _ = setup(db_path)
    customer, master, other, admin = (login(app, email) for email in ('customer@demo.local', 'elena@demo.local', 'master@demo.local', 'admin@demo.local'))
    catalog = customer.get('/api/service-catalog').json()
    assert len(catalog) == 30
    assert sum(item['category'] == 'cleaning' for item in catalog) == 15
    assert sum(item['category'] == 'master' for item in catalog) == 15
    payload = {'catalog_service_id': catalog[0]['id'], 'contact_name': 'Тестовый Заказчик', 'phone': '+7 999 123-45-67', 'address': 'Москва, Тверская улица, дом 7', 'description': 'Нужно провести генеральную уборку квартиры после ремонта.', 'request_key': 'catalog-request-unique-0001'}
    created = customer.post('/api/service-requests', headers=HEADERS, json=payload)
    assert created.status_code == 201, created.text
    rid = created.json()['id']
    assert customer.post('/api/service-requests', headers=HEADERS, json=payload).json()['id'] == rid
    assert [r['id'] for r in customer.get('/api/service-requests').json()] == [rid]
    assert not other.get('/api/service-requests').json()
    assert not master.get('/api/service-requests').json()
    assert [r['id'] for r in admin.get('/api/service-requests').json()] == [rid]
    endpoint = f'/api/service-requests/{rid}/action'
    assert customer.post(endpoint, headers=HEADERS, json={'action': 'assign', 'master_id': 2}).status_code == 403
    assert admin.post(endpoint, headers=HEADERS, json={'action': 'assign', 'master_id': 2}).status_code == 422
    assert admin.post(endpoint, headers=HEADERS, json={'action': 'assign', 'master_id': 4}).json()['status'] == 'assigned'
    assert [r['id'] for r in master.get('/api/service-requests').json()] == [rid]
    assert master.post(endpoint, headers=HEADERS, json={'action': 'accept'}).json()['status'] == 'accepted'
    assert master.post(endpoint, headers=HEADERS, json={'action': 'start'}).json()['status'] == 'in_progress'
    assert master.post(endpoint, headers=HEADERS, json={'action': 'finish'}).json()['status'] == 'awaiting_confirmation'
    assert customer.post(endpoint, headers=HEADERS, json={'action': 'confirm'}).json()['status'] == 'completed'


def test_original_request_chat_review_notifications_and_permissions(db_path):
    app, _ = setup(db_path)
    customer, master, other, admin = (login(app, email) for email in ('customer@demo.local', 'elena@demo.local', 'master@demo.local', 'admin@demo.local'))
    saved_before = {item['user_id'] for item in customer.get('/api/favorites').json()}
    assert customer.post('/api/favorites/4', headers=HEADERS).status_code == 200
    assert {item['user_id'] for item in customer.get('/api/favorites').json()} == saved_before | {4}
    assert master.post('/api/favorites/4', headers=HEADERS).status_code == 403
    assert customer.delete('/api/favorites/4', headers=HEADERS).status_code == 200
    assert {item['user_id'] for item in customer.get('/api/favorites').json()} == saved_before
    created = customer.post('/api/service-requests', headers=HEADERS, json={
        'catalog_service_id': 1, 'contact_name': 'Тестовый заказчик', 'phone': '+7 999 123-45-67',
        'address': 'Москва, Тверская улица, дом 7', 'description': 'Нужна тщательная уборка квартиры после ремонта.',
        'request_key': 'chat-review-request-0001',
    })
    assert created.status_code == 201, created.text
    rid = created.json()['id']
    assert any(str(rid) in n['text'] for n in admin.get('/api/notifications').json())
    chat = f'/api/service-requests/{rid}/messages'
    review = f'/api/service-requests/{rid}/review'
    assert admin.get(chat).status_code == 403
    assert admin.post(chat, headers=HEADERS, json={'body': 'Не участник переписки'}).status_code == 403
    assert customer.post(chat, headers=HEADERS, json={'body': 'До назначения'}).status_code == 409
    assert admin.post(f'/api/service-requests/{rid}/action', headers=HEADERS, json={'action': 'assign', 'master_id': 4}).status_code == 200
    assert other.get(chat).status_code == 404
    assert other.post(chat, headers=HEADERS, json={'body': 'Посторонний'}).status_code == 404
    assert customer.post(chat, headers=HEADERS, json={'body': 'Уточните время приезда'}).status_code == 201
    assert master.post(chat, headers=HEADERS, json={'body': 'Согласуем время в чате'}).status_code == 201
    assert [m['body'] for m in master.get(chat).json()] == ['Уточните время приезда', 'Согласуем время в чате']
    assert any('Новое сообщение' in n['text'] for n in master.get('/api/notifications').json())
    assert master.post('/api/notifications/read', headers=HEADERS).status_code == 200
    assert all(n['is_read'] for n in master.get('/api/notifications').json())
    assert customer.post(review, headers=HEADERS, json={'rating': 5, 'body': 'Отличная работа'}).status_code == 409
    assert master.patch('/api/profile', headers=HEADERS, json={'name': 'Елена новое имя', 'phone': ''}).status_code == 200
    action_url = f'/api/service-requests/{rid}/action'
    assert master.post(action_url, headers=HEADERS, json={'action': 'accept'}).status_code == 409
    assert admin.post('/api/admin/masters/4/moderate', headers=HEADERS, json={'status': 'approved'}).status_code == 200
    for action_name in ('accept', 'start', 'finish'):
        assert master.post(action_url, headers=HEADERS, json={'action': action_name}).status_code == 200
    assert customer.post(action_url, headers=HEADERS, json={'action': 'confirm'}).status_code == 200
    assert master.post(chat, headers=HEADERS, json={'body': 'После закрытия'}).status_code == 409
    assert len(customer.get(chat).json()) == 2
    assert customer.post(review, headers=HEADERS, json={'rating': 5, 'body': 'Отличная работа'}).status_code == 201
    assert customer.post(review, headers=HEADERS, json={'rating': 4, 'body': 'Повтор'}).status_code == 409
    assert other.post(review, headers=HEADERS, json={'rating': 1, 'body': 'Чужой отзыв'}).status_code == 403
    assert customer.get('/api/service-requests').json()[0]['has_review'] == 1
    assert not master.get('/api/masters/4').json()['reviews']
    admin_reviews = admin.get('/api/admin/overview').json()['request_reviews']
    assert len(admin_reviews) == 1 and admin_reviews[0]['visible'] == 0
    assert admin.patch(f"/api/admin/service-request-reviews/{admin_reviews[0]['id']}", headers=HEADERS, json={'visible': True}).status_code == 200
    public_master = customer.get('/api/masters/4').json()
    assert public_master['review_count'] == 1 and public_master['rating'] == 5
    assert public_master['reviews'][0]['body'] == 'Отличная работа'
    assert 'reason' not in public_master


def test_real_registration_rejection_and_reassignment(db_path, monkeypatch):
    answers = iter(['owner@example.test', 'Оператор'])
    passwords = iter(['StrongLocalPass2026', 'StrongLocalPass2026'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr('getpass.getpass', lambda _: next(passwords))
    initialize(db_path)
    app = create_app(db_path)
    admin = TestClient(app)
    assert admin.post('/api/auth/login', headers=HEADERS, json={'email': 'owner@example.test', 'password': 'StrongLocalPass2026'}).status_code == 200
    customer, master, replacement = (TestClient(app) for _ in range(3))
    for client, email, role in ((customer, 'customer@example.test', 'customer'), (master, 'first@example.test', 'master'), (replacement, 'second@example.test', 'master')):
        response = client.post('/api/auth/register', headers=HEADERS, json={'email': email, 'name': 'Тестовый пользователь', 'password': 'StrongLocalPass2026', 'role': role})
        assert response.status_code == 201, response.text
    profile = {'title': 'Мастер по уборке', 'bio': 'Выполняю уборку квартир после ремонта и согласовываю состав работ заранее.', 'city': 'Москва', 'district': 'Центр', 'experience': 3, 'available': True}
    for client in (master, replacement):
        assert client.put('/api/master/profile', headers=HEADERS, json=profile).status_code == 200
        assert client.post('/api/master/services', headers=HEADERS, json={'category_id': 3, 'title': 'Уборка квартиры', 'price': 3000, 'duration': 120}).status_code == 201
        assert client.put('/api/master/availability', headers=HEADERS, json={'windows': [{'weekday': 0, 'start': 540, 'end': 1080}]}).status_code == 200
    for uid in (3, 4):
        assert admin.post(f'/api/admin/masters/{uid}/moderate', headers=HEADERS, json={'status': 'approved'}).status_code == 200
    catalog = customer.get('/api/service-catalog').json()
    request = customer.post('/api/service-requests', headers=HEADERS, json={'catalog_service_id': catalog[0]['id'], 'contact_name': 'Тестовый заказчик', 'phone': '+7 999 123-45-67', 'address': 'Москва, Тверская улица, дом 7', 'description': 'Нужно убрать квартиру после ремонта в ближайшие дни.', 'request_key': 'real-flow-request-key-001'})
    assert request.status_code == 201, request.text
    rid = request.json()['id']
    endpoint = f'/api/service-requests/{rid}/action'
    assert admin.post(endpoint, headers=HEADERS, json={'action': 'assign', 'master_id': 3}).json()['status'] == 'assigned'
    assert master.post(endpoint, headers=HEADERS, json={'action': 'reject', 'reason': 'Не успеваю по срокам'}).json()['status'] == 'pending'
    assert customer.get('/api/service-requests').json()[0]['master_id'] is None
    assert admin.post(endpoint, headers=HEADERS, json={'action': 'assign', 'master_id': 4}).json()['status'] == 'assigned'
    assert replacement.post(endpoint, headers=HEADERS, json={'action': 'accept'}).json()['status'] == 'accepted'
    assert replacement.post(endpoint, headers=HEADERS, json={'action': 'start'}).json()['status'] == 'in_progress'
    assert replacement.post(endpoint, headers=HEADERS, json={'action': 'finish'}).json()['status'] == 'awaiting_confirmation'
    assert customer.post(endpoint, headers=HEADERS, json={'action': 'confirm'}).json()['status'] == 'completed'


def test_empty_schedule_removes_master_from_catalog_and_assignment(db_path):
    app, _ = setup(db_path)
    customer, master, admin = (login(app, email) for email in ('customer@demo.local', 'elena@demo.local', 'admin@demo.local'))
    assert 4 in [m['user_id'] for m in customer.get('/api/masters').json()]
    assert master.put('/api/master/availability', headers=HEADERS, json={'windows': []}).status_code == 200
    assert 4 not in [m['user_id'] for m in customer.get('/api/masters').json()]
    created = customer.post('/api/service-requests', headers=HEADERS, json={'catalog_service_id': 1, 'contact_name': 'Тестовый заказчик', 'phone': '+7 999 123-45-67', 'address': 'Москва, Тверская улица, дом 7', 'description': 'Нужно провести генеральную уборку после ремонта.', 'request_key': 'empty-schedule-request-001'})
    assert created.status_code == 201
    response = admin.post(f"/api/service-requests/{created.json()['id']}/action", headers=HEADERS, json={'action': 'assign', 'master_id': 4})
    assert response.status_code == 404


def test_proxy_clients_have_independent_auth_limits(db_path):
    app, _ = setup(db_path)
    client = TestClient(app, client=('127.0.0.1', 50000))
    wrong = {'email': 'customer@demo.local', 'password': 'WrongPassword123'}
    for _ in range(15):
        assert client.post('/api/auth/login', headers={**HEADERS, 'X-Real-IP': '198.51.100.11'}, json=wrong).status_code == 401
    assert client.post('/api/auth/login', headers={**HEADERS, 'X-Real-IP': '198.51.100.11'}, json=wrong).status_code == 429
    assert client.post('/api/auth/login', headers={**HEADERS, 'X-Real-IP': '198.51.100.12'}, json={'email': 'customer@demo.local', 'password': 'DemoPass2026!'}).status_code == 200


def test_order_lifecycle_and_isolation(db_path):
    app, path = setup(db_path)
    customer, master, other, admin = (login(app, x) for x in ('customer@demo.local', 'master@demo.local', 'elena@demo.local', 'admin@demo.local'))
    created = order(customer, 'my-unique-order-key')
    assert created.status_code == 201, created.text
    oid = created.json()['id']
    assert order(customer, 'my-unique-order-key').json()['id'] == oid
    assert other.get(f'/api/orders/{oid}').status_code == 404
    assert action(other, oid, 'accept').status_code == 404
    assert action(customer, oid, 'accept').status_code == 403
    assert action(master, oid, 'accept').json()['status'] == 'accepted'
    assert action(master, oid, 'accept').status_code == 409
    assert customer.post(f'/api/orders/{oid}/messages', headers=HEADERS, json={'body': 'Домофон не работает'}).status_code == 201
    assert other.get(f'/api/orders/{oid}/messages').status_code == 404
    picture = io.BytesIO()
    Image.new('RGB', (20, 20), '#35604b').save(picture, format='PNG')
    uploaded = customer.post(f'/api/orders/{oid}/photos', headers=HEADERS, files={'file': ('sample.png', picture.getvalue(), 'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    photo_id = uploaded.json()['id']
    assert customer.get('/media/' + photo_id).status_code == 200
    assert other.get('/media/' + photo_id).status_code == 404
    assert customer.delete('/api/photos/' + photo_id, headers=HEADERS).status_code == 200
    assert not (path.parent / 'uploads' / (photo_id + '.webp')).exists()
    assert action(master, oid, 'start').json()['status'] == 'in_progress'
    assert action(master, oid, 'finish').json()['status'] == 'awaiting_confirmation'
    assert action(customer, oid, 'confirm').json()['status'] == 'completed'
    assert customer.post(f'/api/orders/{oid}/review', headers=HEADERS, json={'rating': 5, 'body': 'Хорошая работа'}).status_code == 201
    assert customer.post(f'/api/orders/{oid}/review', headers=HEADERS, json={'rating': 5, 'body': 'Ещё один'}).status_code == 409
    assert admin.get('/api/admin/overview').json()['stats']['completed'] >= 1
    assert master.get('/api/admin/overview').status_code == 403
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM events WHERE order_id=?', (oid,)).fetchone()[0] == 5


def test_moderation_sessions_availability_and_dispute(db_path):
    app, _ = setup(db_path)
    customer, master, pending, admin = (login(app, x) for x in ('customer@demo.local', 'master@demo.local', 'pending@demo.local', 'admin@demo.local'))
    assert 8 not in [m['user_id'] for m in customer.get('/api/masters').json()]
    payload = {'title': 'Ремонт бытовой техники', 'bio': 'Диагностика и ремонт техники с предварительным согласованием задачи и стоимости работ.', 'city': 'Москва', 'district': 'Сокольники', 'experience': 4, 'available': True}
    assert pending.put('/api/master/profile', headers=HEADERS, json=payload).status_code == 200
    assert admin.post('/api/admin/masters/8/moderate', headers=HEADERS, json={'status': 'approved'}).status_code == 200
    assert 8 in [m['user_id'] for m in customer.get('/api/masters').json()]
    assert pending.put('/api/master/availability', headers=HEADERS, json={'windows': [{'weekday': 0, 'start': 540, 'end': 660}, {'weekday': 0, 'start': 600, 'end': 720}]}).status_code == 422
    assert customer.post('/api/auth/register', json={'email': 'abc@local.test', 'password': 'LongPassword123', 'name': 'Тест'}).status_code == 403
    assert customer.post('/api/auth/register', headers=HEADERS, json={'email': 'abc@local.test', 'password': 'LongPassword123', 'name': 'Тест', 'role': 'admin'}).status_code == 422
    created = order(customer, 'dispute-order-key')
    assert created.status_code == 201, created.text
    oid = created.json()['id']
    assert action(master, oid, 'accept').status_code == 200
    assert action(master, oid, 'start').status_code == 200
    assert action(customer, oid, 'dispute', 'Работа не соответствует описанию').json()['status'] == 'disputed'
    assert action(admin, oid, 'resolve_cancel', 'После рассмотрения переписки').json()['status'] == 'cancelled'
    assert customer.post(f'/api/orders/{oid}/review', headers=HEADERS, json={'rating': 1, 'body': 'Плохо'}).status_code == 409
    assert customer.post('/api/tickets', headers=HEADERS, json={'subject': 'Вопрос по работе', 'body': 'Прошу проверить историю заявки'}).status_code == 201
    assert admin.post('/api/admin/tickets/1/reply', headers=HEADERS, json={'reply': 'История заявки рассмотрена'}).status_code == 200
    assert customer.get('/api/tickets').json()[0]['status'] == 'closed'
    assert admin.patch('/api/admin/users/8', headers=HEADERS, json={'active': False}).status_code == 200
    assert pending.get('/api/auth/me').status_code == 401


def test_search_service_and_first_admin_setup(db_path, monkeypatch):
    answers = iter(['operator@example.test', 'Оператор'])
    passwords = iter(['StrongLocalPass2026', 'StrongLocalPass2026'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr('getpass.getpass', lambda _: next(passwords))
    initialize(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM categories').fetchone()[0] == 6
    with pytest.raises(SystemExit, match='уже существует'):
        initialize(db_path)

    demo_path = db_path.with_name(db_path.stem + '-demo.sqlite3')
    seed(demo_path)
    app = create_app(demo_path, True)
    client = TestClient(app)
    result = client.get('/api/masters', params={'q': 'смеситель'})
    assert result.status_code == 200
    assert [m['user_id'] for m in result.json()] == [2]
    service = client.get('/api/services/1')
    assert service.status_code == 200
    assert service.json()['master_id'] == 2
    assert client.get('/api/services/9').status_code == 404  # Pending master is not public.
    admin = login(app, 'admin@demo.local')
    newcomer = TestClient(app)
    registered = newcomer.post('/api/auth/register', headers=HEADERS, json={
        'email': 'new-master@example.test', 'password': 'LongPassword123',
        'name': 'Новый мастер', 'role': 'master',
    })
    assert registered.status_code == 201, registered.text
    new_id = registered.json()['id']
    profile = {
        'title': 'Мастер по ремонту', 'bio': 'Занимаюсь ремонтом и заранее согласовываю стоимость работ с заказчиком.',
        'city': 'Москва', 'district': 'Центр', 'experience': 2, 'available': True,
    }
    assert newcomer.put('/api/master/profile', headers=HEADERS, json=profile).status_code == 200
    assert admin.post(f'/api/admin/masters/{new_id}/moderate', headers=HEADERS, json={'status': 'approved'}).status_code == 422


def test_pending_quota_and_service_moderation(db_path):
    app, _ = setup(db_path)
    customer = login(app, 'customer@demo.local')
    master = login(app, 'master@demo.local')
    choices = []
    for offset in range(2, 20):
        day = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=offset)).date().isoformat()
        choices += customer.get('/api/services/1/slots', params={'date': day}).json()
        if len(choices) >= 12:
            break
    assert len(choices) >= 12
    choices = choices[::2]
    assert len(choices) >= 6
    for i, scheduled_at in enumerate(choices[:5]):
        response = customer.post('/api/orders', headers=HEADERS, json={
            'service_id': 1, 'address': 'Москва, Тверская, дом 7',
            'description': 'Пожалуйста, замените смеситель на кухне.',
            'scheduled_at': scheduled_at, 'request_key': f'quota-request-{i:04d}',
        })
        assert response.status_code == 201, response.text
    blocked = customer.post('/api/orders', headers=HEADERS, json={
        'service_id': 1, 'address': 'Москва, Тверская, дом 7',
        'description': 'Пожалуйста, замените смеситель на кухне.',
        'scheduled_at': choices[5], 'request_key': 'quota-request-six',
    })
    assert blocked.status_code == 429
    assert customer.get('/api/masters/2').json()['available'] == 1
    service = master.post('/api/master/services', headers=HEADERS, json={
        'category_id': 1, 'title': 'Новая работа', 'description': 'Согласование перед заказом',
        'price': 2000, 'duration': 60, 'active': True,
    })
    assert service.status_code == 201, service.text
    assert 2 not in [m['user_id'] for m in customer.get('/api/masters').json()]


def test_demo_seed_rejects_application_database():
    with pytest.raises(SystemExit, match='отдельную базу'):
        seed(Path(__file__).resolve().parents[1] / 'data' / 'app.sqlite3')


def test_original_pages_are_served_and_legacy_scripts_are_blocked(db_path):
    app = create_app(db_path)
    client = TestClient(app)
    for route, marker in (
        ('/', 'assets/js/index.js'),
        ('/application', 'assets/js/application-safe.js'),
        ('/account', 'assets/js/account-safe.js'),
        ('/master', 'assets/js/master-safe.js'),
        ('/masters', 'assets/js/masters-safe.js'),
        ('/admin', 'assets/js/admin-safe.js'),
        ('/about', 'assets/js/site-safe.js'),
        ('/support', 'assets/js/support-safe.js'),
        ('/referral', 'assets/js/site-safe.js'),
        ('/roadmap', 'assets/js/site-safe.js'),
    ):
        response = client.get(route)
        assert response.status_code == 200, (route, response.text[:200])
        assert marker in response.text, route
        if route != '/':
            assert 'assets/css/mobile-pages.css' in response.text, route
            assert 'assets/js/mobile-pages.js' in response.text, route
    assert client.get('/assets/js/masters-safe.js').status_code == 200
    assert client.get('/assets/js/support-safe.js').status_code == 200
    assert client.get('/assets/js/mobile-pages.js').status_code == 200
    for path in (
        '/assets/js/application.js', '/assets/js/account.js',
        '/assets/js/admin.js', '/assets/js/test-page.js',
        '/static/app.js', '/testi/test_page.html',
        '/my-firebase-functions.zip', '/platform/data/app.sqlite3',
    ):
        assert client.get(path).status_code == 404, path
