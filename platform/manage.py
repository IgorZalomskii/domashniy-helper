"""One-time operator setup for a non-demo installation. Never accepts a password in argv."""
import argparse
import getpass
import sqlite3
from pathlib import Path

from app import create_app, hash_password, now


def initialize(database):
    path = Path(database)
    create_app(path)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            raise SystemExit('Администратор уже существует. Повторная инициализация запрещена.')
        email = input('Email администратора: ').strip().lower()
        name = input('Имя администратора: ').strip()
        password = getpass.getpass('Пароль (минимум 10 символов): ')
        repeat = getpass.getpass('Повторите пароль: ')
        if '@' not in email or '.' not in email.split('@')[-1] or len(name) < 2 or len(password) < 10 or password != repeat:
            raise SystemExit('Некорректные данные или пароли не совпадают. Ничего не сохранено.')
        if conn.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            raise SystemExit('Пользователь с этим email уже существует.')
        conn.execute('INSERT INTO users(email,password,name,role,created_at) VALUES(?,?,?,?,?)',
                     (email, hash_password(password), name, 'admin', now()))
        defaults = [('Сантехника', 'water'), ('Электрика', 'bolt'), ('Уборка', 'sparkles'),
                    ('Сборка мебели', 'chair'), ('Мелкий ремонт', 'tool'), ('Бытовая техника', 'washer')]
        for category, icon in defaults:
            conn.execute('INSERT OR IGNORE INTO categories(name,icon) VALUES(?,?)', (category, icon))
    print('Администратор и базовые категории созданы.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Создать первого администратора без демонстрационных данных')
    parser.add_argument('--database', default=str(Path(__file__).parent / 'data' / 'app.sqlite3'))
    initialize(parser.parse_args().database)
