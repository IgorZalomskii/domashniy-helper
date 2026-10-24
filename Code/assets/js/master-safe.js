(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const dayNames = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
  const statusNames = {
    pending: 'Ожидает назначения', assigned: 'Назначена', accepted: 'Принята',
    in_progress: 'В работе', awaiting_confirmation: 'Ждёт подтверждения заказчика',
    completed: 'Завершена', cancelled: 'Отменена', rejected: 'Отклонена'
  };
  let settings;
  let categories = [];

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value != null) item.textContent = value;
    return item;
  }

  function message(text, error = false) {
    const target = $('#global-message');
    target.textContent = text;
    target.dataset.error = String(error);
    target.hidden = false;
    target.scrollIntoView({ block: 'nearest' });
  }

  async function api(path, method = 'GET', data) {
    const options = { method, credentials: 'same-origin', headers: { 'X-Requested-With': 'home-helper' } };
    if (data instanceof FormData) options.body = data;
    else if (data !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(data);
    }
    const response = await fetch('/api' + path, options);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = result.detail;
      throw new Error(typeof detail === 'string' ? detail : 'Не удалось выполнить действие. Попробуйте ещё раз.');
    }
    return result;
  }

  function busy(button, active) {
    button.disabled = active;
    if (active) button.dataset.label = button.textContent;
    button.textContent = active ? 'Подождите…' : button.dataset.label;
  }

  function labelValue(label, value) {
    const p = node('p', '', '');
    p.append(node('strong', '', label + ': '), document.createTextNode(String(value || '—')));
    return p;
  }

  function actionButton(label, action) {
    const button = node('button', 'master-outline', label);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  }

  async function loadRequests() {
    const container = $('#request-list');
    container.replaceChildren(node('p', '', 'Загрузка заявок…'));
    try {
      const requests = await api('/service-requests');
      container.replaceChildren();
      if (!requests.length) {
        container.append(node('p', 'master-hint', 'Пока заявок нет. После назначения администратором они появятся здесь.'));
        return;
      }
      for (const request of requests) {
        const card = node('article', 'master-item');
        card.append(node('h3', '', request.service_name));
        const meta = node('div', 'master-item-meta');
        meta.append(node('span', 'master-status', statusNames[request.status] || request.status));
        meta.append(node('span', '', 'Заявка №' + request.id));
        meta.append(node('span', '', new Date(request.created_at).toLocaleString('ru-RU')));
        card.append(meta, labelValue('Заказчик', request.contact_name), labelValue('Телефон', request.phone),
          labelValue('Адрес', request.address), labelValue('Задача', request.description));
        if (['assigned','accepted','in_progress','awaiting_confirmation','completed'].includes(request.status)) { const chatBox = node('div', 'master-chat'); const chat = actionButton('Открыть чат', async () => { chatBox.replaceChildren(node('p', '', 'Загрузка сообщений…')); try { const messages = await api('/service-requests/' + request.id + '/messages'); chatBox.replaceChildren(...messages.map(m => labelValue(m.name || 'Сообщение', m.body))); if (!['completed','cancelled','rejected'].includes(request.status)) { const form = document.createElement('form'); form.innerHTML = '<label>Сообщение <input name="body" required maxlength="3000"></label><button type="submit">Отправить</button>'; form.addEventListener('submit', async e => { e.preventDefault(); try { await api('/service-requests/' + request.id + '/messages', 'POST', { body: form.elements.body.value }); chat.click(); } catch (err) { message(err.message, true); } }); chatBox.append(form); } } catch (e) { chatBox.replaceChildren(node('p', 'master-message', e.message)); } }); card.append(chat, chatBox); }
        if (request.reason) card.append(labelValue('Причина', request.reason));
        const choices = {
          assigned: [['Принять', 'accept'], ['Отклонить', 'reject']],
          accepted: [['Начать работу', 'start']],
          in_progress: [['Работа выполнена', 'finish']]
        }[request.status] || [];
        if (choices.length) {
          const actions = node('div', 'master-item-actions');
          for (const [title, action] of choices) actions.append(actionButton(title, async () => {
            const reason = action === 'reject' ? window.prompt('Укажите причину отказа (не менее 5 символов)') : '';
            if (action === 'reject' && reason === null) return;
            try {
              await api('/service-requests/' + request.id + '/action', 'POST', { action, reason: reason || '' });
              message('Статус заявки обновлён.');
              await loadRequests();
            } catch (error) { message(error.message, true); }
          }));
          card.append(actions);
        }
        container.append(card);
      }
    } catch (error) {
      container.replaceChildren(node('p', 'master-message', error.message));
    }
  }

  async function loadNotifications() { const list=$('#notifications-list'); try { const items=await api('/notifications'); list.replaceChildren(...(items.length ? items.map(n => node('p','',`${n.is_read ? '' : '• '}${n.text || ''}`)) : [node('p','master-hint','Уведомлений нет.')])); } catch (e) { list.replaceChildren(node('p','master-message',e.message)); } }

  function renderProfile() {
    const profile = settings.profile;
    const form = $('#profile-form');
    for (const key of ['title', 'city', 'district', 'experience', 'bio']) form.elements[key].value = profile[key] ?? '';
    form.elements.available.checked = Boolean(profile.available);
    const states = { pending: 'Анкета ожидает проверки администратором.', approved: 'Анкета опубликована.', rejected: 'Анкета отклонена.' };
    $('#moderation').textContent = (states[profile.moderation] || '') + (profile.reason ? ' Причина: ' + profile.reason : '');
  }

  function renderServices() {
    const container = $('#service-list');
    container.replaceChildren();
    if (!settings.services.length) container.append(node('p', 'master-hint', 'Услуг пока нет.'));
    for (const service of settings.services) {
      const card = node('article', 'master-item');
      card.append(node('h3', '', service.title));
      card.append(node('p', 'master-hint', categories.find(c => c.id === service.category_id)?.name || 'Категория'));
      card.append(node('p', '', service.description));
      const meta = node('div', 'master-item-meta');
      meta.append(node('span', '', service.price.toLocaleString('ru-RU') + ' ₽'),
        node('span', '', service.duration + ' мин.'), node('span', '', service.active ? 'Активна' : 'Скрыта'));
      card.append(meta, actionButton('Редактировать', () => {
        const form = $('#service-form');
        for (const key of ['category_id', 'title', 'description', 'price', 'duration']) form.elements[key].value = service[key];
        form.elements.service_id.value = service.id;
        form.elements.active.checked = Boolean(service.active);
        $('#service-form-title').textContent = 'Редактировать услугу';
        $('#service-reset').hidden = false;
        form.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        form.elements.title.focus({ preventScroll: true });
      }));
      container.append(card);
    }
  }

  function renderSchedule() {
    const container = $('#schedule-days');
    container.replaceChildren();
    for (let i = 0; i < 7; i++) {
      const saved = settings.windows.find(item => item.weekday === i);
      const row = node('div', 'master-day');
      const label = node('label', '', dayNames[i]);
      const enabled = node('input');
      enabled.type = 'checkbox';
      enabled.name = 'enabled-' + i;
      enabled.checked = Boolean(saved);
      label.prepend(enabled);
      row.append(label);
      for (const [key, fallback] of [['start', 540], ['end', 1080]]) {
        const field = node('input');
        field.type = 'time';
        field.name = key + '-' + i;
        field.setAttribute('aria-label', (key === 'start' ? 'Начало ' : 'Конец ') + dayNames[i]);
        const minutes = saved?.[key] ?? fallback;
        field.value = String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
        row.append(field);
      }
      container.append(row);
    }
  }

  function renderPortfolio() {
    const container = $('#portfolio-list');
    container.replaceChildren();
    if (!settings.portfolio.length) container.append(node('p', 'master-hint', 'Фото пока нет.'));
    for (const photo of settings.portfolio) {
      const card = node('div', 'master-photo');
      const image = node('img');
      image.src = '/media/' + encodeURIComponent(photo.id);
      image.alt = photo.caption || 'Пример работы мастера';
      image.loading = 'lazy';
      const remove = actionButton('Удалить фото', async () => {
        if (!window.confirm('Удалить это фото?')) return;
        try { await api('/photos/' + encodeURIComponent(photo.id), 'DELETE'); await loadSettings(); message('Фото удалено.'); }
        catch (error) { message(error.message, true); }
      });
      card.append(image, remove);
      container.append(card);
    }
  }

  async function loadSettings() {
    settings = await api('/master/settings');
    renderProfile();
    renderServices();
    renderSchedule();
    renderPortfolio();
  }

  async function init() {
    let user;
    try { user = await api('/auth/me'); }
    catch { location.assign('/account?next=/master'); return; }
    if (user.role !== 'master') { location.assign(user.role === 'admin' ? '/admin' : '/account'); return; }
    $('#welcome').textContent = user.name + ' · ' + user.email;
    categories = await api('/categories');
    const categoryField = $('#service-form').elements.category_id;
    categoryField.replaceChildren();
    for (const category of categories) {
      const option = node('option', '', category.name);
      option.value = category.id;
      categoryField.append(option);
    }
    await Promise.all([loadSettings(), loadRequests(), loadNotifications()]);
  }

  $('#logout').addEventListener('click', async () => {
    try { await api('/auth/logout', 'POST'); location.assign('/'); }
    catch (error) { message(error.message, true); }
  });
  $('#reload-requests').addEventListener('click', loadRequests);
  $('#mark-notifications-read').addEventListener('click', async () => { try { await api('/notifications/read','POST'); await loadNotifications(); message('Уведомления отмечены прочитанными.'); } catch (e) { message(e.message,true); } });
  $('#profile-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type=submit]');
    busy(button, true);
    try {
      await api('/master/profile', 'PUT', {
        title: form.elements.title.value, city: form.elements.city.value,
        district: form.elements.district.value, bio: form.elements.bio.value,
        experience: Number(form.elements.experience.value), available: form.elements.available.checked
      });
      await loadSettings();
      message('Анкета сохранена. Изменения могут потребовать повторной проверки.');
    } catch (error) { message(error.message, true); }
    finally { busy(button, false); }
  });
  $('#service-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type=submit]');
    busy(button, true);
    try {
      const id = form.elements.service_id.value;
      await api(id ? '/master/services/' + id : '/master/services', id ? 'PUT' : 'POST', {
        category_id: Number(form.elements.category_id.value), title: form.elements.title.value,
        description: form.elements.description.value, price: Number(form.elements.price.value),
        duration: Number(form.elements.duration.value), active: form.elements.active.checked
      });
      form.reset();
      form.elements.service_id.value = '';
      $('#service-form-title').textContent = 'Добавить услугу';
      $('#service-reset').hidden = true;
      await loadSettings();
      message('Услуга сохранена.');
    } catch (error) { message(error.message, true); }
    finally { busy(button, false); }
  });
  $('#service-reset').addEventListener('click', () => {
    $('#service-form').reset();
    $('#service-form').elements.service_id.value = '';
    $('#service-form-title').textContent = 'Добавить услугу';
    $('#service-reset').hidden = true;
  });
  $('#schedule-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const windows = [];
    for (let i = 0; i < 7; i++) {
      if (!form.elements['enabled-' + i].checked) continue;
      const toMinutes = value => { const [hours, minutes] = value.split(':').map(Number); return hours * 60 + minutes; };
      windows.push({ weekday: i, start: toMinutes(form.elements['start-' + i].value), end: toMinutes(form.elements['end-' + i].value) });
    }
    const button = form.querySelector('button[type=submit]');
    busy(button, true);
    try { await api('/master/availability', 'PUT', { windows }); await loadSettings(); message('График сохранён.'); }
    catch (error) { message(error.message, true); }
    finally { busy(button, false); }
  });
  $('#portfolio-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = form.elements.file.files[0];
    if (!file || file.size > 5 * 1024 * 1024) { message('Выберите фото размером до 5 МБ.', true); return; }
    const button = form.querySelector('button[type=submit]');
    busy(button, true);
    try {
      const data = new FormData();
      data.append('file', file);
      await api('/master/portfolio', 'POST', data);
      form.reset();
      await loadSettings();
      message('Фото добавлено.');
    } catch (error) { message(error.message, true); }
    finally { busy(button, false); }
  });
  init().catch(error => message(error.message, true));
})();
