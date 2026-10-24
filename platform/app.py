"""Same-origin local services platform. Money is stored as integer RUB, time as UTC ISO.

SQLite transactions serialize reservations and lifecycle transitions. This version is
intended for a single application process; see README before public deployment.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict, deque
from typing import Literal
import argparse
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time

from fastapi import FastAPI, Request, Response, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, field_validator
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
MOSCOW = timezone(timedelta(hours=3))
ACTIVE = ('pending', 'accepted', 'in_progress', 'awaiting_confirmation', 'disputed')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(value, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + hashed


def check_password(value, stored):
    return hmac.compare_digest(hash_password(value, stored.split(':')[0]), stored)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=5, max_length=180)
    password: str = Field(min_length=10, max_length=128)

    @field_validator('email')
    @classmethod
    def email_valid(cls, v):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', v):
            raise ValueError('Укажите корректный email')
        return v.lower()


class Register(Login):
    name: str = Field(min_length=2, max_length=90)
    role: Literal['customer', 'master'] = 'customer'


class Profile(Input):
    name: str = Field(min_length=2, max_length=90)
    phone: str = Field(max_length=25, default='')


class Password(Input):
    current: str = Field(min_length=1, max_length=128)
    new: str = Field(min_length=10, max_length=128)


class MasterProfile(Input):
    title: str = Field(min_length=4, max_length=100)
    bio: str = Field(min_length=30, max_length=3000)
    city: str = Field(min_length=2, max_length=80)
    district: str = Field(min_length=2, max_length=80)
    experience: int = Field(ge=0, le=60)
    available: bool = True


class Service(Input):
    category_id: int
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(max_length=1500, default='')
    price: int = Field(ge=0, le=1000000)
    duration: int = Field(ge=30, le=480)
    active: bool = True


class Window(Input):
    weekday: int = Field(ge=0, le=6)
    start: int = Field(ge=0, le=1380)
    end: int = Field(ge=30, le=1440)


class Schedule(Input):
    windows: list[Window] = Field(max_length=28)


class Order(Input):
    service_id: int
    address: str = Field(min_length=8, max_length=300)
    description: str = Field(min_length=15, max_length=3000)
    scheduled_at: datetime
    request_key: str = Field(min_length=16, max_length=80)


class Action(Input):
    action: Literal['accept', 'reject', 'start', 'finish', 'confirm', 'cancel', 'dispute', 'resolve_complete', 'resolve_cancel']
    reason: str = Field(max_length=1500, default='')


class Message(Input):
    body: str = Field(min_length=1, max_length=3000)


class Review(Message):
    rating: int = Field(ge=1, le=5)


class Moderation(Input):
    status: Literal['approved', 'rejected']
    reason: str = Field(max_length=800, default='')


class UserState(Input):
    active: bool


class Visibility(Input):
    visible: bool


class Category(Input):
    name: str = Field(min_length=2, max_length=60)
    active: bool = True


class Ticket(Input):
    subject: str = Field(min_length=4, max_length=100)
    body: str = Field(min_length=10, max_length=3000)


class TicketReply(Input):
    reply: str = Field(min_length=5, max_length=3000)


class ServiceRequest(Input):
    catalog_service_id: int
    contact_name: str = Field(min_length=2, max_length=90)
    phone: str = Field(min_length=7, max_length=25)
    address: str = Field(min_length=8, max_length=300)
    description: str = Field(min_length=15, max_length=3000)
    desired_at: datetime | None = None
    request_key: str = Field(min_length=16, max_length=80)


class RequestAction(Input):
    action: Literal['assign', 'accept', 'reject', 'start', 'finish', 'confirm', 'cancel']
    master_id: int | None = None
    reason: str = Field(max_length=1500, default='')


def create_app(database=None, demo=False):
    app = FastAPI(title='Домашний помощник API', version='1.0.0')
    app.state.db = Path(database or os.getenv('APP_DB', str(ROOT / 'data' / 'app.sqlite3')))
    app.state.db.parent.mkdir(parents=True, exist_ok=True)
    app.state.uploads = app.state.db.parent / 'uploads'
    app.state.uploads.mkdir(exist_ok=True)
    app.state.demo = demo
    app.state.limits = defaultdict(deque)
    allowed = set(os.getenv('APP_ORIGINS', 'http://127.0.0.1:8765,http://localhost:8765').split(','))
    secure = os.getenv('COOKIE_SECURE', '0') == '1'

    @contextmanager
    def db(write=False):
        con = sqlite3.connect(app.state.db, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        catalog = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
        for category, group in catalog.items():
            for item in group['items']:
                c.execute('INSERT OR IGNORE INTO catalog_services(category,name,description,price_label,icon) VALUES(?,?,?,?,?)', (category, item['name'], item['description'], item['price'], item['icon']))

    def one(c, sql, args=()):
        row = c.execute(sql, args).fetchone()
        if row is None:
            raise HTTPException(404, 'Не найдено')
        return dict(row)

    def rows(c, sql, args=()):
        return [dict(r) for r in c.execute(sql, args).fetchall()]

    def audit(c, user, action, target):
        c.execute('INSERT INTO audit(actor_id,action,target,created_at) VALUES(?,?,?,?)', (user['id'], action, str(target), now()))

    def notify(c, user_id, text, order_id=None):
        c.execute('INSERT INTO notifications(user_id,order_id,text,created_at) VALUES(?,?,?,?)', (user_id, order_id, text, now()))

    def current(request: Request):
        token = request.cookies.get('session', '')
        with db() as c:
            r = c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1', (digest(token), int(time.time()))).fetchone()
        if not r:
            raise HTTPException(401, 'Войдите в аккаунт')
        return dict(r)

    def role(user, expected):
        if user['role'] != expected:
            raise HTTPException(403, 'Недостаточно прав')

    def public_user(user):
        return {k: user[k] for k in ('id', 'name', 'email', 'phone', 'role', 'active')}

    def access(c, order_id, user):
        order = one(c, 'SELECT * FROM orders WHERE id=?', (order_id,))
        if user['role'] != 'admin' and user['id'] not in (order['customer_id'], order['master_id']):
            raise HTTPException(404, 'Заявка не найдена')
        return order

    def request_access(c, request_id, user):
        item = one(c, 'SELECT * FROM service_requests WHERE id=?', (request_id,))
        if user['role'] != 'admin' and user['id'] not in (item['customer_id'], item['master_id']):
            raise HTTPException(404, 'Заявка не найдена')
        return item

    def public_master(item):
        fields = ('user_id', 'name', 'title', 'bio', 'city', 'district', 'experience', 'available', 'has_schedule', 'rating', 'review_count', 'price_from', 'can_cleaning', 'can_master')
        return {key: item[key] for key in fields}

    def rate(request, bucket, limit=15):
        address = request.client.host if request.client else 'local'
        if address in ('127.0.0.1', '::1'):
            forwarded = request.headers.get('x-real-ip', '')
            try:
                address = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        key = (address, bucket)
        limits = app.state.limits
        if len(limits) > 5000:
            for k in list(limits):
                if not limits[k] or limits[k][-1] < time.time() - 60:
                    del limits[k]
        q = limits[key]
        while q and q[0] < time.time() - 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, 'Слишком много попыток. Попробуйте через минуту.')
        q.append(time.time())

    @app.middleware('http')
    async def guard(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if request.headers.get('x-requested-with') != 'home-helper' or (origin and origin not in allowed):
                return JSONResponse({'detail': 'Недопустимый источник запроса'}, 403)
            length = request.headers.get('content-length')
            if length is None:
                if request.headers.get('transfer-encoding'):
                    return JSONResponse({'detail': 'Укажите размер запроса'}, 411)
                length = '0'
            if not length.isdigit() or int(length) > 6 * 1024 * 1024:
                return JSONResponse({'detail': 'Файл слишком большой (до 5 МБ)'}, 413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' https://cdnjs.cloudflare.com; font-src 'self' https://cdnjs.cloudflare.com; script-src 'self'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity(request, exc):
        return JSONResponse({'detail': 'Такая запись уже существует или данные связаны с другой записью'}, 409)

    @app.get('/api/health')
    def health():
        with db() as c:
            c.execute('SELECT 1')
        return {'status': 'ok', 'demo': demo, 'timezone': 'Europe/Moscow'}

    def session(c, response, user_id):
        token = secrets.token_urlsafe(40)
        c.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
        c.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), user_id, int(time.time()) + 86400))
        response.set_cookie('session', token, httponly=True, secure=secure, samesite='lax', max_age=86400)

    @app.post('/api/auth/register', status_code=201)
    def register(data: Register, request: Request, response: Response):
        rate(request, 'auth')
        hashed = hash_password(data.password)
        with db(True) as c:
            uid = c.execute('INSERT INTO users(email,password,name,role,created_at) VALUES(?,?,?,?,?)', (data.email, hashed, data.name, data.role, now())).lastrowid
            if data.role == 'master':
                c.execute('INSERT INTO masters(user_id) VALUES(?)', (uid,))
            session(c, response, uid)
            return public_user(one(c, 'SELECT * FROM users WHERE id=?', (uid,)))

    dummy = hash_password(secrets.token_hex(20))

    @app.post('/api/auth/login')
    def login(data: Login, request: Request, response: Response):
        rate(request, 'auth')
        with db(True) as c:
            r = c.execute('SELECT * FROM users WHERE email=?', (data.email,)).fetchone()
            valid = check_password(data.password, r['password'] if r else dummy)
            if not r or not valid or not r['active']:
                raise HTTPException(401, 'Неверный email или пароль, либо аккаунт заблокирован')
            session(c, response, r['id'])
            return public_user(dict(r))

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response):
        with db(True) as c:
            c.execute('DELETE FROM sessions WHERE token=?', (digest(request.cookies.get('session', '')),))
        response.delete_cookie('session')
        return {'ok': True}

    @app.get('/api/auth/me')
    def me(user=Depends(current)):
        return public_user(user)

    @app.patch('/api/profile')
    def profile(data: Profile, user=Depends(current)):
        with db(True) as c:
            c.execute('UPDATE users SET name=?,phone=? WHERE id=?', (data.name, data.phone, user['id']))
            if user['role'] == 'master' and data.name != user['name']:
                c.execute("UPDATE masters SET moderation='pending',reason='' WHERE user_id=? AND moderation='approved'", (user['id'],))
            return public_user(one(c, 'SELECT * FROM users WHERE id=?', (user['id'],)))

    @app.post('/api/profile/password')
    def password(data: Password, request: Request, response: Response, user=Depends(current)):
        rate(request, 'password', 5)
        if not check_password(data.current, user['password']):
            raise HTTPException(400, 'Текущий пароль неверен')
        hashed = hash_password(data.new)
        with db(True) as c:
            c.execute('UPDATE users SET password=? WHERE id=?', (hashed, user['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            session(c, response, user['id'])
        return {'ok': True}

    @app.get('/api/categories')
    def categories():
        with db() as c:
            return rows(c, 'SELECT * FROM categories WHERE active=1 ORDER BY id')

    @app.get('/api/service-catalog')
    def service_catalog():
        with db() as c:
            return rows(c, 'SELECT id,category,name,description,price_label,icon FROM catalog_services WHERE active=1 ORDER BY id')

    @app.post('/api/service-requests', status_code=201)
    def create_service_request(data: ServiceRequest, request: Request, user=Depends(current)):
        role(user, 'customer')
        rate(request, 'service-requests', 10)
        if data.desired_at is not None:
            if data.desired_at.tzinfo is None or not datetime.now(timezone.utc) + timedelta(hours=1) < data.desired_at.astimezone(timezone.utc) < datetime.now(timezone.utc) + timedelta(days=90):
                raise HTTPException(422, 'Выберите время от часа до 90 дней вперёд')
        if not re.fullmatch(r'[+\d ()-]{7,25}', data.phone):
            raise HTTPException(422, 'Укажите корректный номер телефона')
        with db(True) as c:
            old = c.execute('SELECT id FROM service_requests WHERE customer_id=? AND request_key=?', (user['id'], data.request_key)).fetchone()
            if old:
                return {'id': old['id']}
            one(c, 'SELECT id FROM catalog_services WHERE id=? AND active=1', (data.catalog_service_id,))
            if c.execute("SELECT COUNT(*) FROM service_requests WHERE customer_id=? AND status IN ('pending','assigned')", (user['id'],)).fetchone()[0] >= 5:
                raise HTTPException(429, 'У вас уже пять активных заявок. Дождитесь ответа или отмените одну из них.')
            stamp = now()
            rid = c.execute('INSERT INTO service_requests(customer_id,catalog_service_id,contact_name,phone,address,description,desired_at,created_at,updated_at,request_key) VALUES(?,?,?,?,?,?,?,?,?,?)', (user['id'], data.catalog_service_id, data.contact_name, data.phone, data.address, data.description, data.desired_at.astimezone(timezone.utc).isoformat(timespec='seconds') if data.desired_at else None, stamp, stamp, data.request_key)).lastrowid
            audit(c, user, 'service_request:create', rid)
            for admin in c.execute("SELECT id FROM users WHERE role='admin' AND active=1"):
                notify(c, admin['id'], 'Новая заявка №' + str(rid))
        return {'id': rid}

    @app.get('/api/service-requests')
    def service_requests(user=Depends(current)):
        with db() as c:
            where, args = ('', ()) if user['role'] == 'admin' else (' WHERE r.customer_id=?' if user['role'] == 'customer' else ' WHERE r.master_id=?', (user['id'],))
            return rows(c, 'SELECT r.id,r.customer_id,r.master_id,r.contact_name,r.phone,r.address,r.description,r.desired_at,r.status,r.reason,r.created_at,s.name service_name,s.category catalog_category,s.price_label,u.name master_name,EXISTS(SELECT 1 FROM service_request_reviews v WHERE v.service_request_id=r.id) has_review FROM service_requests r JOIN catalog_services s ON s.id=r.catalog_service_id LEFT JOIN users u ON u.id=r.master_id' + where + ' ORDER BY r.id DESC LIMIT 200', args)

    def qualified_master(c, master_id, catalog_service_id):
        ready = c.execute("SELECT m.user_id FROM masters m JOIN users u ON u.id=m.user_id WHERE m.user_id=? AND m.moderation='approved' AND m.available=1 AND u.active=1 AND EXISTS(SELECT 1 FROM availability a WHERE a.master_id=m.user_id)", (master_id,)).fetchone()
        group = one(c, 'SELECT category FROM catalog_services WHERE id=?', (catalog_service_id,))['category']
        qualified = c.execute("SELECT 1 FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=? AND s.active=1 AND ca.active=1 AND ((?='cleaning' AND ca.name='Уборка') OR (?='master' AND ca.name!='Уборка')) LIMIT 1", (master_id, group, group)).fetchone()
        return bool(ready and qualified)

    @app.post('/api/service-requests/{rid}/action')
    def service_request_action(rid: int, data: RequestAction, user=Depends(current)):
        with db(True) as c:
            req = request_access(c, rid, user)
            transitions = {'assign': ('admin', ['pending','assigned'], 'assigned'), 'accept': ('master', ['assigned'], 'accepted'), 'reject': ('master', ['assigned'], 'pending'), 'start': ('master', ['accepted'], 'in_progress'), 'finish': ('master', ['in_progress'], 'awaiting_confirmation'), 'confirm': ('customer', ['awaiting_confirmation'], 'completed'), 'cancel': ('customer', ['pending','assigned','accepted'], 'cancelled')}
            expected, source, target = transitions[data.action]
            role(user, expected)
            if req['status'] not in source:
                raise HTTPException(409, 'Действие недоступно для текущего статуса')
            master_id = req['master_id']
            if data.action == 'assign':
                if data.master_id is None:
                    raise HTTPException(422, 'Выберите мастера')
                one(c, "SELECT m.user_id FROM masters m JOIN users u ON u.id=m.user_id WHERE m.user_id=? AND m.moderation='approved' AND m.available=1 AND u.active=1 AND EXISTS(SELECT 1 FROM availability a WHERE a.master_id=m.user_id)", (data.master_id,))
                if not qualified_master(c, data.master_id, req['catalog_service_id']):
                    raise HTTPException(422, 'Мастер не выполняет услуги выбранного раздела')
                master_id = data.master_id
            if data.action == 'accept' and not qualified_master(c, user['id'], req['catalog_service_id']):
                raise HTTPException(409, 'Анкета мастера больше не готова к приёму заявок')
            if data.action in ('reject','cancel') and len(data.reason) < 5:
                raise HTTPException(422, 'Укажите причину (минимум 5 символов)')
            if data.action == 'reject':
                master_id = None
            c.execute('UPDATE service_requests SET master_id=?,status=?,reason=?,updated_at=? WHERE id=?', (master_id, target, data.reason, now(), rid))
            audit(c, user, 'service_request:' + data.action, rid)
            if data.action == 'assign' and req['master_id'] and req['master_id'] != master_id:
                notify(c, req['master_id'], 'Назначение по заявке №' + str(rid) + ' изменено')
            for uid in {req['customer_id'], master_id} - {user['id'], None}:
                notify(c, uid, 'Заявка №' + str(rid) + ': статус изменён')
        return {'status': target}

    @app.get('/api/service-requests/{rid}/messages')
    def request_messages(rid: int, user=Depends(current)):
        if user['role'] not in ('customer', 'master'):
            raise HTTPException(403, 'Недостаточно прав')
        with db() as c:
            item = request_access(c, rid, user)
            if item['master_id'] is None:
                return []
            sql = 'SELECT m.id,m.author_id,m.body,m.created_at,u.name FROM service_request_messages m JOIN users u ON u.id=m.author_id WHERE m.service_request_id=?'
            args = [rid]
            if user['role'] == 'master':
                sql += ' AND m.master_id=?'
                args.append(user['id'])
            return rows(c, sql + ' ORDER BY m.id DESC LIMIT 200', args)[::-1]

    @app.post('/api/service-requests/{rid}/messages', status_code=201)
    def request_message(rid: int, data: Message, request: Request, user=Depends(current)):
        if user['role'] not in ('customer', 'master'):
            raise HTTPException(403, 'Недостаточно прав')
        rate(request, 'messages', 40)
        with db(True) as c:
            item = request_access(c, rid, user)
            if item['master_id'] is None or item['status'] not in ('assigned', 'accepted', 'in_progress', 'awaiting_confirmation'):
                raise HTTPException(409, 'Переписка недоступна для текущего статуса заявки')
            mid = c.execute('INSERT INTO service_request_messages(service_request_id,master_id,author_id,body,created_at) VALUES(?,?,?,?,?)', (rid, item['master_id'], user['id'], data.body, now())).lastrowid
            for uid in {item['customer_id'], item['master_id']} - {user['id']}:
                notify(c, uid, 'Новое сообщение в заявке №' + str(rid))
        return {'id': mid}

    @app.post('/api/service-requests/{rid}/review', status_code=201)
    def request_review(rid: int, data: Review, user=Depends(current)):
        role(user, 'customer')
        with db(True) as c:
            item = request_access(c, rid, user)
            if item['status'] != 'completed' or item['master_id'] is None:
                raise HTTPException(409, 'Отзыв доступен после подтверждения выполнения')
            c.execute('INSERT INTO service_request_reviews(service_request_id,customer_id,master_id,rating,body,created_at) VALUES(?,?,?,?,?,?)', (rid, user['id'], item['master_id'], data.rating, data.body, now()))
            notify(c, item['master_id'], 'Заказчик оставил отзыв по заявке №' + str(rid))
            audit(c, user, 'service_request:review', rid)
        return {'ok': True}

    master_select = '''SELECT m.*,u.name,u.active,EXISTS(SELECT 1 FROM availability a WHERE a.master_id=m.user_id) has_schedule,ROUND((SELECT AVG(rating) FROM (SELECT rating FROM reviews r WHERE r.master_id=m.user_id AND r.visible=1 UNION ALL SELECT rating FROM service_request_reviews rr WHERE rr.master_id=m.user_id AND rr.visible=1)),1) rating,
        ((SELECT COUNT(*) FROM reviews r WHERE r.master_id=m.user_id AND r.visible=1)+(SELECT COUNT(*) FROM service_request_reviews rr WHERE rr.master_id=m.user_id AND rr.visible=1)) review_count,
        (SELECT MIN(s.price) FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=m.user_id AND s.active=1 AND ca.active=1) price_from,
        EXISTS(SELECT 1 FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=m.user_id AND s.active=1 AND ca.active=1 AND ca.name='Уборка') can_cleaning,
        EXISTS(SELECT 1 FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=m.user_id AND s.active=1 AND ca.active=1 AND ca.name!='Уборка') can_master
        FROM masters m JOIN users u ON u.id=m.user_id'''

    @app.get('/api/masters')
    def masters(q: str = '', category: int = 0, district: str = '', sort: str = 'rating'):
        q, district = q[:100], district[:80]
        # A small inflection-tolerant prefix covers common Russian queries such as
        # "смеситель" → "смесителя" without adding a search service or dependency.
        term = q[:-1] if len(q) >= 6 else q
        with db() as c:
            sql = master_select + """ WHERE m.moderation='approved' AND m.available=1 AND u.active=1
                AND EXISTS(SELECT 1 FROM availability a WHERE a.master_id=m.user_id)
                AND (u.name LIKE ? OR m.title LIKE ? OR m.bio LIKE ? OR EXISTS(
                    SELECT 1 FROM services qs JOIN categories qc ON qc.id=qs.category_id
                    WHERE qs.master_id=m.user_id AND qs.active=1 AND qc.active=1
                    AND (qs.title LIKE ? OR qs.description LIKE ? OR qc.name LIKE ?)))
                AND m.district LIKE ?"""
            args = [f'%{term}%'] * 6 + [f'%{district}%']
            if category:
                sql += ' AND EXISTS(SELECT 1 FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=m.user_id AND s.active=1 AND ca.active=1 AND s.category_id=?)'
                args.append(category)
            sql += ' ORDER BY ' + {'price': 'price_from IS NULL,price_from', 'experience': 'm.experience DESC'}.get(sort, 'rating IS NULL,rating DESC,review_count DESC') + ' LIMIT 100'
            return [public_master(item) for item in rows(c, sql, args)]

    @app.get('/api/masters/{uid}')
    def master_detail(uid: int):
        with db() as c:
            m = one(c, master_select + " WHERE m.user_id=? AND m.moderation='approved' AND m.available=1 AND u.active=1 AND EXISTS(SELECT 1 FROM availability a WHERE a.master_id=m.user_id)", (uid,))
            m['services'] = rows(c, 'SELECT s.*,ca.name category FROM services s JOIN categories ca ON ca.id=s.category_id WHERE s.master_id=? AND s.active=1 AND ca.active=1', (uid,))
            m['reviews'] = rows(c, '''SELECT rating,body,created_at,name FROM (
                SELECT r.rating,r.body,r.created_at,u.name FROM reviews r JOIN users u ON u.id=r.customer_id WHERE r.master_id=? AND r.visible=1
                UNION ALL SELECT rr.rating,rr.body,rr.created_at,u.name FROM service_request_reviews rr JOIN users u ON u.id=rr.customer_id WHERE rr.master_id=? AND rr.visible=1
                ) ORDER BY created_at DESC LIMIT 50''', (uid, uid))
            m['portfolio'] = rows(c, "SELECT id,caption FROM attachments WHERE owner_id=? AND kind='portfolio'", (uid,))
            return public_master(m) | {'services': m['services'], 'reviews': m['reviews'], 'portfolio': m['portfolio']}

    @app.get('/api/services/{sid}')
    def service_detail(sid: int):
        with db() as c:
            return one(c, """SELECT s.*,ca.name category,u.name master_name,m.title master_title,
                m.city,m.district,m.experience FROM services s
                JOIN categories ca ON ca.id=s.category_id
                JOIN masters m ON m.user_id=s.master_id
                JOIN users u ON u.id=m.user_id
                WHERE s.id=? AND s.active=1 AND ca.active=1 AND m.moderation='approved'
                AND m.available=1 AND u.active=1""", (sid,))

    @app.get('/api/master/settings')
    def settings(user=Depends(current)):
        role(user, 'master')
        with db() as c:
            return {'profile': one(c, 'SELECT * FROM masters WHERE user_id=?', (user['id'],)), 'services': rows(c, 'SELECT * FROM services WHERE master_id=? ORDER BY id', (user['id'],)), 'windows': rows(c, 'SELECT weekday,start,end FROM availability WHERE master_id=? ORDER BY weekday,start', (user['id'],)), 'portfolio': rows(c, "SELECT id,caption FROM attachments WHERE owner_id=? AND kind='portfolio'", (user['id'],))}

    @app.put('/api/master/profile')
    def master_profile(data: MasterProfile, user=Depends(current)):
        role(user, 'master')
        with db(True) as c:
            old = one(c, 'SELECT * FROM masters WHERE user_id=?', (user['id'],))
            changed = any(old[k] != getattr(data, k) for k in ('title', 'bio', 'city', 'district', 'experience'))
            c.execute('UPDATE masters SET title=?,bio=?,city=?,district=?,experience=?,available=?,moderation=?,reason=? WHERE user_id=?', (data.title, data.bio, data.city, data.district, data.experience, data.available, 'pending' if changed else old['moderation'], '' if changed else old['reason'], user['id']))
        return {'ok': True, 'moderation': 'pending' if changed else old['moderation']}

    @app.post('/api/master/services', status_code=201)
    def add_service(data: Service, user=Depends(current)):
        role(user, 'master')
        with db(True) as c:
            one(c, 'SELECT id FROM categories WHERE id=? AND active=1', (data.category_id,))
            if c.execute('SELECT COUNT(*) FROM services WHERE master_id=?', (user['id'],)).fetchone()[0] >= 30:
                raise HTTPException(400, 'Не более 30 услуг')
            sid = c.execute('INSERT INTO services(master_id,category_id,title,description,price,duration,active) VALUES(?,?,?,?,?,?,?)', (user['id'], data.category_id, data.title, data.description, data.price, data.duration, data.active)).lastrowid
            c.execute("UPDATE masters SET moderation='pending',reason='' WHERE user_id=? AND moderation='approved'", (user['id'],))
        return {'id': sid}

    @app.put('/api/master/services/{sid}')
    def edit_service(sid: int, data: Service, user=Depends(current)):
        role(user, 'master')
        with db(True) as c:
            old = one(c, 'SELECT * FROM services WHERE id=? AND master_id=?', (sid, user['id']))
            one(c, 'SELECT id FROM categories WHERE id=? AND active=1', (data.category_id,))
            c.execute('UPDATE services SET category_id=?,title=?,description=?,price=?,duration=?,active=? WHERE id=?', (data.category_id, data.title, data.description, data.price, data.duration, data.active, sid))
            if any(old[key] != getattr(data, key) for key in ('category_id', 'title', 'description', 'price', 'duration', 'active')):
                c.execute("UPDATE masters SET moderation='pending',reason='' WHERE user_id=? AND moderation='approved'", (user['id'],))
        return {'ok': True}

    @app.put('/api/master/availability')
    def availability(data: Schedule, user=Depends(current)):
        role(user, 'master')
        windows = sorted(data.windows, key=lambda x: (x.weekday, x.start))
        for i, w in enumerate(windows):
            if w.start >= w.end or (i and windows[i-1].weekday == w.weekday and windows[i-1].end > w.start):
                raise HTTPException(422, 'Интервалы пересекаются или время окончания раньше начала')
        with db(True) as c:
            c.execute('DELETE FROM availability WHERE master_id=?', (user['id'],))
            c.executemany('INSERT INTO availability(master_id,weekday,start,end) VALUES(?,?,?,?)', [(user['id'], w.weekday, w.start, w.end) for w in windows])
        return {'ok': True}

    def free(c, service, dt):
        local = dt.astimezone(MOSCOW)
        start = local.hour * 60 + local.minute
        if local.second or start % 30:
            return False
        windows = rows(c, 'SELECT start,end FROM availability WHERE master_id=? AND weekday=?', (service['master_id'], local.weekday()))
        if not any(w['start'] <= start and start + service['duration'] <= w['end'] for w in windows):
            return False
        end = dt + timedelta(minutes=service['duration'])
        busy = rows(c, "SELECT scheduled_at,duration FROM orders WHERE master_id=? AND status IN ('pending','accepted','in_progress','awaiting_confirmation','disputed')", (service['master_id'],))
        return not any(dt < datetime.fromisoformat(o['scheduled_at']) + timedelta(minutes=o['duration']) and end > datetime.fromisoformat(o['scheduled_at']) for o in busy)

    @app.get('/api/services/{sid}/slots')
    def slots(sid: int, date: str):
        try:
            day = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=MOSCOW)
        except ValueError:
            raise HTTPException(422, 'Неверная дата')
        with db() as c:
            s = one(c, "SELECT s.* FROM services s JOIN masters m ON m.user_id=s.master_id JOIN users u ON u.id=m.user_id JOIN categories ca ON ca.id=s.category_id WHERE s.id=? AND s.active=1 AND ca.active=1 AND m.moderation='approved' AND m.available=1 AND u.active=1", (sid,))
            utc = datetime.now(timezone.utc)
            return [dt.isoformat() for i in range(48) if utc + timedelta(hours=1) < (dt := day + timedelta(minutes=i*30)) < utc + timedelta(days=90) and free(c, s, dt)]

    @app.post('/api/orders', status_code=201)
    def new_order(data: Order, request: Request, user=Depends(current)):
        role(user, 'customer')
        rate(request, 'orders', 10)
        if data.scheduled_at.tzinfo is None:
            raise HTTPException(422, 'Укажите часовой пояс даты')
        dt = data.scheduled_at.astimezone(timezone.utc)
        utc = datetime.now(timezone.utc)
        if not utc + timedelta(hours=1) < dt < utc + timedelta(days=90):
            raise HTTPException(422, 'Выберите время от часа до 90 дней вперёд')
        with db(True) as c:
            old = c.execute('SELECT id FROM orders WHERE customer_id=? AND request_key=?', (user['id'], data.request_key)).fetchone()
            if old:
                return {'id': old['id']}
            pending = c.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND status='pending' AND scheduled_at>?", (user['id'], utc.isoformat())).fetchone()[0]
            if pending >= 5:
                raise HTTPException(429, 'Не более пяти заявок ожидают ответа мастеров. Дождитесь ответа или отмените одну из них.')
            s = one(c, "SELECT s.* FROM services s JOIN masters m ON m.user_id=s.master_id JOIN users u ON u.id=m.user_id JOIN categories ca ON ca.id=s.category_id WHERE s.id=? AND s.active=1 AND ca.active=1 AND m.moderation='approved' AND m.available=1 AND u.active=1", (data.service_id,))
            if not free(c, s, dt):
                raise HTTPException(409, 'Это время уже занято или недоступно. Выберите другое.')
            oid = c.execute('INSERT INTO orders(customer_id,master_id,service_id,service_title,price,duration,address,description,scheduled_at,created_at,updated_at,request_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (user['id'], s['master_id'], s['id'], s['title'], s['price'], s['duration'], data.address, data.description, dt.isoformat(timespec='seconds'), now(), now(), data.request_key)).lastrowid
            c.execute('INSERT INTO events(order_id,actor_id,action,created_at) VALUES(?,?,?,?)', (oid, user['id'], 'created', now()))
            notify(c, s['master_id'], 'Новая заявка: ' + s['title'], oid)
        return {'id': oid}

    @app.get('/api/orders')
    def orders(user=Depends(current)):
        with db() as c:
            where, args = ('', ()) if user['role'] == 'admin' else (' WHERE o.customer_id=?' if user['role'] == 'customer' else ' WHERE o.master_id=?', (user['id'],))
            return rows(c, 'SELECT o.*,cu.name customer_name,mu.name master_name FROM orders o JOIN users cu ON cu.id=o.customer_id JOIN users mu ON mu.id=o.master_id' + where + ' ORDER BY o.id DESC LIMIT 200', args)

    @app.get('/api/orders/{oid}')
    def order_detail(oid: int, user=Depends(current)):
        with db() as c:
            o = access(c, oid, user)
            o['customer'] = one(c, 'SELECT id,name,phone FROM users WHERE id=?', (o['customer_id'],))
            o['master'] = one(c, 'SELECT id,name,phone FROM users WHERE id=?', (o['master_id'],))
            o['events'] = rows(c, 'SELECT e.*,u.name FROM events e JOIN users u ON u.id=e.actor_id WHERE e.order_id=? ORDER BY e.id', (oid,))
            o['review'] = rows(c, 'SELECT * FROM reviews WHERE order_id=?', (oid,))
            o['attachments'] = rows(c, 'SELECT id,caption,owner_id FROM attachments WHERE order_id=?', (oid,))
            return o

    @app.post('/api/orders/{oid}/action')
    def order_action(oid: int, data: Action, user=Depends(current)):
        with db(True) as c:
            o = access(c, oid, user)
            transitions = {
                'accept': ('master', ['pending'], 'accepted'), 'reject': ('master', ['pending'], 'rejected'),
                'start': ('master', ['accepted'], 'in_progress'), 'finish': ('master', ['in_progress'], 'awaiting_confirmation'),
                'confirm': ('customer', ['awaiting_confirmation'], 'completed'), 'cancel': ('customer', ['pending', 'accepted'], 'cancelled'),
                'dispute': ('customer', ['in_progress', 'awaiting_confirmation'], 'disputed'),
                'resolve_complete': ('admin', ['disputed'], 'completed'), 'resolve_cancel': ('admin', ['disputed'], 'cancelled')}
            expected, sources, target = transitions[data.action]
            role(user, expected)
            if o['status'] not in sources:
                raise HTTPException(409, 'Статус уже изменился или действие недоступно')
            if data.action in ('reject', 'cancel', 'dispute', 'resolve_complete', 'resolve_cancel') and len(data.reason) < 5:
                raise HTTPException(422, 'Укажите причину (минимум 5 символов)')
            if data.action == 'accept':
                one(c, "SELECT user_id FROM masters WHERE user_id=? AND moderation='approved'", (user['id'],))
            c.execute('UPDATE orders SET status=?,reason=?,updated_at=? WHERE id=?', (target, data.reason, now(), oid))
            c.execute('INSERT INTO events(order_id,actor_id,action,note,created_at) VALUES(?,?,?,?,?)', (oid, user['id'], data.action, data.reason, now()))
            for uid in (o['customer_id'], o['master_id']):
                if uid != user['id']:
                    notify(c, uid, 'Статус заявки №' + str(oid) + ' изменён', oid)
            audit(c, user, 'order:' + data.action, oid)
        return {'status': target}

    @app.get('/api/orders/{oid}/messages')
    def messages(oid: int, user=Depends(current)):
        with db() as c:
            access(c, oid, user)
            return rows(c, 'SELECT m.*,u.name FROM messages m JOIN users u ON u.id=m.author_id WHERE order_id=? ORDER BY m.id DESC LIMIT 200', (oid,))[::-1]

    @app.post('/api/orders/{oid}/messages', status_code=201)
    def message(oid: int, data: Message, request: Request, user=Depends(current)):
        rate(request, 'messages', 40)
        with db(True) as c:
            o = access(c, oid, user)
            if o['status'] not in ('pending', 'accepted', 'in_progress', 'awaiting_confirmation'):
                raise HTTPException(409, 'Переписка закрыта для этой заявки')
            mid = c.execute('INSERT INTO messages(order_id,author_id,body,created_at) VALUES(?,?,?,?)', (oid, user['id'], data.body, now())).lastrowid
            for uid in (o['customer_id'], o['master_id']):
                if uid != user['id']:
                    notify(c, uid, 'Новое сообщение в заявке №' + str(oid), oid)
        return {'id': mid}

    @app.post('/api/orders/{oid}/review', status_code=201)
    def review(oid: int, data: Review, user=Depends(current)):
        role(user, 'customer')
        with db(True) as c:
            o = access(c, oid, user)
            if o['status'] != 'completed':
                raise HTTPException(409, 'Отзыв доступен после подтверждения выполнения')
            c.execute('INSERT INTO reviews(order_id,customer_id,master_id,rating,body,visible,created_at) VALUES(?,?,?,?,?,?,?)', (oid, user['id'], o['master_id'], data.rating, data.body, 0, now()))
            notify(c, o['master_id'], 'Заказчик оставил отзыв', oid)
        return {'ok': True}

    @app.get('/api/favorites')
    def favorites(user=Depends(current)):
        with db() as c:
            return [public_master(item) for item in rows(c, master_select + " JOIN favorites f ON f.master_id=m.user_id WHERE f.user_id=? AND m.moderation='approved' AND u.active=1", (user['id'],))]

    @app.post('/api/favorites/{uid}')
    def favorite(uid: int, user=Depends(current)):
        role(user, 'customer')
        with db(True) as c:
            one(c, "SELECT user_id FROM masters WHERE user_id=? AND moderation='approved'", (uid,))
            c.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)', (user['id'], uid))
        return {'ok': True}

    @app.delete('/api/favorites/{uid}')
    def unfavorite(uid: int, user=Depends(current)):
        with db(True) as c:
            c.execute('DELETE FROM favorites WHERE user_id=? AND master_id=?', (user['id'], uid))
        return {'ok': True}

    @app.get('/api/notifications')
    def notifications(user=Depends(current)):
        with db() as c:
            return rows(c, 'SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100', (user['id'],))

    @app.post('/api/notifications/read')
    def read_notifications(user=Depends(current)):
        with db(True) as c:
            c.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (user['id'],))
        return {'ok': True}

    @app.post('/api/tickets', status_code=201)
    def ticket(data: Ticket, request: Request, user=Depends(current)):
        rate(request, 'tickets', 5)
        with db(True) as c:
            tid = c.execute('INSERT INTO tickets(user_id,subject,body,created_at) VALUES(?,?,?,?)', (user['id'], data.subject, data.body, now())).lastrowid
        return {'id': tid}

    @app.get('/api/tickets')
    def tickets(user=Depends(current)):
        with db() as c:
            return rows(c, 'SELECT t.*,u.name FROM tickets t JOIN users u ON u.id=t.user_id' + ('' if user['role'] == 'admin' else ' WHERE user_id=?') + ' ORDER BY t.id DESC LIMIT 100', () if user['role'] == 'admin' else (user['id'],))

    @app.post('/api/admin/tickets/{tid}/reply')
    def reply(tid: int, data: TicketReply, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            t = one(c, 'SELECT * FROM tickets WHERE id=?', (tid,))
            c.execute("UPDATE tickets SET reply=?,status='closed' WHERE id=?", (data.reply, tid))
            notify(c, t['user_id'], 'Поддержка ответила на обращение №' + str(tid))
            audit(c, user, 'ticket:reply', tid)
        return {'ok': True}

    @app.get('/api/admin/overview')
    def overview(user=Depends(current)):
        role(user, 'admin')
        with db() as c:
            return {'users': rows(c, 'SELECT id,email,name,phone,role,active,created_at FROM users ORDER BY id DESC LIMIT 500'), 'masters': rows(c, master_select + ' ORDER BY m.user_id DESC LIMIT 200'), 'categories': rows(c, 'SELECT * FROM categories ORDER BY id'), 'reviews': rows(c, 'SELECT r.*,u.name FROM reviews r JOIN users u ON u.id=r.customer_id ORDER BY r.id DESC LIMIT 200'), 'request_reviews': rows(c, 'SELECT rr.*,u.name FROM service_request_reviews rr JOIN users u ON u.id=rr.customer_id ORDER BY rr.id DESC LIMIT 200'), 'audit': rows(c, 'SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100'), 'stats': {'users': c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 'orders': c.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 'completed': c.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0], 'pending_masters': c.execute("SELECT COUNT(*) FROM masters WHERE moderation='pending'").fetchone()[0]}}

    @app.post('/api/admin/masters/{uid}/moderate')
    def moderate(uid: int, data: Moderation, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            m = one(c, 'SELECT * FROM masters WHERE user_id=?', (uid,))
            if data.status == 'approved' and (len(m['title']) < 4 or len(m['bio']) < 30 or not m['district']):
                raise HTTPException(422, 'Мастер ещё не заполнил профиль')
            if data.status == 'approved':
                has_service = c.execute('SELECT 1 FROM services s JOIN categories cat ON cat.id=s.category_id WHERE s.master_id=? AND s.active=1 AND cat.active=1 LIMIT 1', (uid,)).fetchone()
                has_schedule = c.execute('SELECT 1 FROM availability WHERE master_id=? LIMIT 1', (uid,)).fetchone()
                if not has_service or not has_schedule:
                    raise HTTPException(422, 'Для публикации добавьте активную услугу и рабочий график')
            if data.status == 'rejected' and len(data.reason) < 5:
                raise HTTPException(422, 'Укажите причину отклонения')
            c.execute('UPDATE masters SET moderation=?,reason=? WHERE user_id=?', (data.status, data.reason, uid))
            notify(c, uid, 'Результат проверки профиля: ' + ('одобрен' if data.status == 'approved' else data.reason))
            audit(c, user, 'master:' + data.status, uid)
        return {'ok': True}

    @app.patch('/api/admin/users/{uid}')
    def user_state(uid: int, data: UserState, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            target = one(c, 'SELECT * FROM users WHERE id=?', (uid,))
            if target['role'] == 'admin':
                raise HTTPException(403, 'Управление администраторами доступно только на сервере')
            c.execute('UPDATE users SET active=? WHERE id=?', (data.active, uid))
            if not data.active:
                c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            audit(c, user, 'user:activate' if data.active else 'user:block', uid)
        return {'ok': True}

    @app.post('/api/admin/categories', status_code=201)
    def category_create(data: Category, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            cid = c.execute('INSERT INTO categories(name,active) VALUES(?,?)', (data.name, data.active)).lastrowid
            audit(c, user, 'category:create', cid)
        return {'id': cid}

    @app.put('/api/admin/categories/{cid}')
    def category_update(cid: int, data: Category, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            one(c, 'SELECT id FROM categories WHERE id=?', (cid,))
            c.execute('UPDATE categories SET name=?,active=? WHERE id=?', (data.name, data.active, cid))
            audit(c, user, 'category:update', cid)
        return {'ok': True}

    @app.patch('/api/admin/reviews/{rid}')
    def moderate_review(rid: int, data: Visibility, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            one(c, 'SELECT id FROM reviews WHERE id=?', (rid,))
            c.execute('UPDATE reviews SET visible=? WHERE id=?', (data.visible, rid))
            audit(c, user, 'review:show' if data.visible else 'review:hide', rid)
        return {'ok': True}

    @app.patch('/api/admin/service-request-reviews/{rid}')
    def moderate_request_review(rid: int, data: Visibility, user=Depends(current)):
        role(user, 'admin')
        with db(True) as c:
            one(c, 'SELECT id FROM service_request_reviews WHERE id=?', (rid,))
            c.execute('UPDATE service_request_reviews SET visible=? WHERE id=?', (data.visible, rid))
            audit(c, user, 'service_request_review:show' if data.visible else 'service_request_review:hide', rid)
        return {'ok': True}

    async def store_image(file, user, oid=None):
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, 'Фото должно быть не более 5 МБ')
        try:
            im = Image.open(io.BytesIO(raw))
            if im.width * im.height > 20_000_000 or im.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError()
            im.load()
            im.thumbnail((1600, 1600))
            output = io.BytesIO()
            im.convert('RGB').save(output, format='WEBP', quality=85)
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise HTTPException(422, 'Используйте корректное фото JPEG, PNG или WebP до 20 Мп')
        aid = secrets.token_hex(20)
        path = app.state.uploads / (aid + '.webp')
        written = False
        try:
            with db(True) as c:
                if oid:
                    if access(c, oid, user)['status'] not in ('pending', 'accepted', 'in_progress', 'awaiting_confirmation'):
                        raise HTTPException(409, 'Загрузка фото недоступна для этой заявки')
                else:
                    role(user, 'master')
                count = c.execute('SELECT COUNT(*) FROM attachments WHERE ' + ('order_id=?' if oid else "owner_id=? AND kind='portfolio'"), (oid or user['id'],)).fetchone()[0]
                if count >= 12:
                    raise HTTPException(400, 'Не более 12 фотографий')
                path.write_bytes(output.getvalue())
                written = True
                c.execute('INSERT INTO attachments(id,order_id,owner_id,kind,created_at) VALUES(?,?,?,?,?)', (aid, oid, user['id'], 'order' if oid else 'portfolio', now()))
                if not oid:
                    c.execute("UPDATE masters SET moderation='pending',reason='' WHERE user_id=? AND moderation='approved'", (user['id'],))
        except Exception:
            if written:
                path.unlink(missing_ok=True)
            raise
        return {'id': aid}

    @app.post('/api/orders/{oid}/photos', status_code=201)
    async def order_photo(oid: int, file: UploadFile = File(...), user=Depends(current)):
        return await store_image(file, user, oid)

    @app.post('/api/master/portfolio', status_code=201)
    async def portfolio(file: UploadFile = File(...), user=Depends(current)):
        return await store_image(file, user)

    @app.delete('/api/photos/{aid}')
    def delete_photo(aid: str, user=Depends(current)):
        with db(True) as c:
            a = one(c, 'SELECT * FROM attachments WHERE id=?', (aid,))
            if a['owner_id'] != user['id'] and user['role'] != 'admin':
                raise HTTPException(403, 'Недостаточно прав')
            c.execute('DELETE FROM attachments WHERE id=?', (aid,))
            if a['kind'] == 'portfolio':
                c.execute("UPDATE masters SET moderation='pending',reason='' WHERE user_id=? AND moderation='approved'", (a['owner_id'],))
        (app.state.uploads / (aid + '.webp')).unlink(missing_ok=True)
        return {'ok': True}

    @app.get('/media/{aid}')
    def photo(aid: str, request: Request):
        if not re.fullmatch('[a-f0-9]{40}', aid):
            raise HTTPException(404)
        with db() as c:
            a = one(c, 'SELECT * FROM attachments WHERE id=?', (aid,))
            if a['kind'] == 'order':
                access(c, a['order_id'], current(request))
            else:
                m = one(c, 'SELECT m.moderation,u.active FROM masters m JOIN users u ON u.id=m.user_id WHERE user_id=?', (a['owner_id'],))
                if m['moderation'] != 'approved' or not m['active']:
                    viewer = current(request)
                    if viewer['id'] != a['owner_id'] and viewer['role'] != 'admin':
                        raise HTTPException(404)
        return FileResponse(app.state.uploads / (aid + '.webp'), media_type='image/webp', headers={'Cache-Control': 'private, no-store'})

    original = ROOT.parent / 'Code'
    import mimetypes
    mimetypes.add_type('image/webp', '.webp')
    app.mount('/assets/css', StaticFiles(directory=original / 'assets' / 'css'), name='original-css')
    app.mount('/assets/images', StaticFiles(directory=original / 'assets' / 'images'), name='original-images')

    safe_scripts = {
        'index.js', 'application-safe.js', 'account-safe.js',
        'admin-safe.js', 'master-safe.js', 'site-safe.js',
        'masters-safe.js', 'support-safe.js', 'mobile-pages.js',
        'home-scene.js', 'residence.js', 'three.module.min.js', 'three.core.min.js'
    }

    @app.get('/assets/js/{script_name}', include_in_schema=False)
    def original_script(script_name: str):
        if script_name not in safe_scripts:
            raise HTTPException(404)
        return FileResponse(original / 'assets' / 'js' / script_name, media_type='text/javascript')

    @app.get('/slides/{slide_name}', include_in_schema=False)
    def original_slide(slide_name: str):
        if slide_name not in {f'slide{i}.png' for i in range(1, 7)}:
            raise HTTPException(404)
        slide = original / 'slides' / slide_name
        if not slide.is_file():
            raise HTTPException(404)
        return FileResponse(slide)

    @app.get('/{path:path}', include_in_schema=False)
    def page(path: str):
        if path.startswith(('api/', 'media/', 'assets/')) or path.endswith(('.zip', '.js', '.css', '.json', '.sqlite3')):
            raise HTTPException(404)
        page_names = {
            '': 'index.html', 'index': 'index.html', 'index.html': 'index.html',
            'application': 'application.html', 'application.html': 'application.html',
            'account': 'account.html', 'account.html': 'account.html',
            'master': 'master.html', 'master.html': 'master.html',
            'masters': 'masters.html', 'masters.html': 'masters.html',
            'admin': 'admin.html', 'admin.html': 'admin.html',
            'about': 'about.html', 'about.html': 'about.html',
            'support': 'support.html', 'support.html': 'support.html',
            'referral': 'referral.html', 'referral.html': 'referral.html',
            'roadmap': 'roadmap.html', 'roadmap.html': 'roadmap.html'
        }
        if path in page_names:
            return FileResponse(original / page_names[path])
        if path in ('login', 'register', 'dashboard'):
            return RedirectResponse('/account', status_code=303)
        if path == 'master/settings':
            return RedirectResponse('/master', status_code=303)
        if path == 'catalog':
            return RedirectResponse('/#services', status_code=303)
        raise HTTPException(404)

    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--database')
    args = parser.parse_args()
    uvicorn.run(create_app(args.database, args.demo), host='127.0.0.1', port=args.port)
