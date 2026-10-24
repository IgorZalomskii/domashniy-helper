"""Explicit local demo bootstrap. Never resets existing users or touches external services."""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app import create_app, hash_password, now


def seed(path):
    path = Path(path).resolve()
    if path == (Path(__file__).parent / 'data' / 'app.sqlite3').resolve():
        raise SystemExit('Для демонстрации укажите отдельную базу, не platform/data/app.sqlite3')
    create_app(path)
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA foreign_keys=ON')
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            print('Database is not empty; demo seed skipped.')
            return
        # These public credentials belong ONLY to the opt-in, local demonstration.
        password = hash_password('DemoPass2026!')
        people = [('customer@demo.local', 'Анна Соколова', 'customer'), ('master@demo.local', 'Алексей Волков', 'master'), ('admin@demo.local', 'Администратор', 'admin'), ('elena@demo.local', 'Елена Морозова', 'master'), ('pavel@demo.local', 'Павел Кузнецов', 'master'), ('irina@demo.local', 'Ирина Лебедева', 'master'), ('dmitry@demo.local', 'Дмитрий Орлов', 'master'), ('pending@demo.local', 'Сергей Петров', 'master')]
        for email, name, role in people:
            c.execute('INSERT INTO users(email,password,name,role,created_at) VALUES(?,?,?,?,?)', (email, password, name, role, now()))
        for name, icon in [('Сантехника', 'water'), ('Электрика', 'bolt'), ('Уборка', 'sparkles'), ('Сборка мебели', 'chair'), ('Мелкий ремонт', 'tool'), ('Бытовая техника', 'washer')]:
            c.execute('INSERT INTO categories(name,icon) VALUES(?,?)', (name, icon))
        profiles = [
            (2, 'Сантехника и забота о доме', 'Помогаю с сантехникой: от замены смесителя до установки раковины. Перед началом обсуждаем задачу, необходимые материалы и стоимость.', 'Хамовники', 8, 'approved'),
            (4, 'Чистота, которую приятно заметить', 'Генеральная и поддерживающая уборка квартир. Внимательно отношусь к поверхностям, обсуждаю средства и особенности вашего дома заранее.', 'Арбат', 6, 'approved'),
            (5, 'Электрика без лишних хлопот', 'Устанавливаю светильники, розетки и выключатели. Начинаю с диагностики, объясняю решение и согласовываю объём работ.', 'Пресненский', 12, 'approved'),
            (6, 'Мебель на своём месте', 'Собираю шкафы, комоды и рабочие столы. Аккуратно работаю с деталями и крепежом, помогаю выбрать расположение мебели.', 'Таганский', 5, 'approved'),
            (7, 'Мелкий ремонт с большим вниманием', 'Навешиваю полки, регулирую двери, устраняю небольшие поломки. Берусь за понятные задачи и заранее обсуждаю ограничения.', 'Хамовники', 10, 'approved'),
            (8, 'Ремонт бытовой техники', 'Диагностика и обслуживание стиральных и посудомоечных машин. Сначала определяю причину неисправности и согласовываю дальнейшие действия.', 'Сокольники', 4, 'pending')]
        for uid, title, bio, district, exp, moderation in profiles:
            c.execute('INSERT INTO masters(user_id,title,bio,district,experience,moderation) VALUES(?,?,?,?,?,?)', (uid, title, bio, district, exp, moderation))
            for day in range(7):
                c.execute('INSERT INTO availability(master_id,weekday,start,end) VALUES(?,?,?,?)', (uid, day, 540, 1080))
        for uid, category, title, price, duration in [(2, 1, 'Замена смесителя', 1800, 60), (2, 1, 'Установка раковины', 3200, 120), (4, 3, 'Поддерживающая уборка', 2800, 120), (4, 3, 'Генеральная уборка', 5600, 240), (5, 2, 'Установка светильника', 1500, 60), (5, 2, 'Замена розетки', 900, 30), (6, 4, 'Сборка комода', 2400, 120), (7, 5, 'Установка полок', 1700, 60), (8, 6, 'Диагностика стиральной машины', 1200, 60)]:
            c.execute('INSERT INTO services(master_id,category_id,title,description,price,duration) VALUES(?,?,?,?,?,?)', (uid, category, title, 'Стоимость работы без материалов. Детали можно обсудить с мастером в заявке.', price, duration))
        scheduled = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        c.execute("INSERT INTO orders(customer_id,master_id,service_id,service_title,price,duration,address,description,scheduled_at,status,created_at,updated_at,request_key) VALUES(1,2,1,'Замена смесителя',1800,60,'Демонстрационный адрес','Демонстрационный завершённый заказ',?,'completed',?,?,?)", (scheduled, now(), now(), 'demo-completed-order'))
        c.execute("INSERT INTO reviews(order_id,customer_id,master_id,rating,body,created_at) VALUES(1,1,2,5,'Демонстрационный отзыв: мастер объяснил порядок работ, заменил смеситель и проверил соединения.',?)", (now(),))
        c.execute('INSERT INTO favorites VALUES(1,2)')
    print('DEMO_SEED_OK — local sample accounts only')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, help='Отдельный путь к демонстрационной SQLite базе')
    args = parser.parse_args()
    seed(args.database)
