(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? '');
  const statusNames = {
    pending: 'Новая', assigned: 'Назначена', accepted: 'Принята',
    rejected: 'Отклонена', in_progress: 'В работе', awaiting_confirmation: 'Ждёт подтверждения',
    completed: 'Завершена', cancelled: 'Отменена'
  };

  let state = { overview: null, requests: [], tickets: [] };

  async function api(path, options = {}) {
    const mutating = !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase());
    const response = await fetch(path, { credentials: 'same-origin', ...options,
      headers: { 'Content-Type': 'application/json', ...(mutating ? { 'X-Requested-With': 'home-helper' } : {}), ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Операция не выполнена');
    return data;
  }

  function notify(text, kind = 'success') {
    const node = document.createElement('div');
    node.className = `notification ${kind}`;
    node.textContent = text;
    document.body.append(node);
    requestAnimationFrame(() => { node.style.transform = 'translateX(0)'; });
    setTimeout(() => { node.style.transform = 'translateX(110%)'; setTimeout(() => node.remove(), 250); }, 3200);
  }

  function empty(tbody, colspan, text) {
    tbody.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = colspan;
    cell.className = 'empty-state';
    cell.textContent = text;
    row.append(cell); tbody.append(row);
  }

  function addMasterSection() {
    if ($('#mastersSection')) return;
    const section = document.createElement('section');
    section.className = 'admin-section'; section.id = 'mastersSection';
    section.innerHTML = `<div class="admin-section-header"><h2 class="admin-section-title"><i class="fas fa-user-cog"></i> Мастера</h2><input class="form-control admin-search" id="masterSearch" placeholder="Поиск мастера..." aria-label="Поиск мастера"></div><div class="table-responsive"><table class="users-table"><thead><tr><th>Мастер</th><th>Город / район</th><th>Услуги</th><th>Рейтинг</th><th>Модерация</th><th>Действия</th></tr></thead><tbody id="mastersTableBody"></tbody></table></div>`;
    $('.admin-content').append(section);
  }

  function addTicketSection() {
    const section = document.createElement('section');
    section.className = 'admin-section'; section.id = 'ticketsSection';
    section.innerHTML = '<div class="admin-section-header"><h2 class="admin-section-title"><i class="fas fa-headset"></i> Обращения в поддержку</h2></div><div class="admin-tickets" id="ticketsList"></div>';
    $('.admin-content').append(section);
  }

  function addReviewSection() {
    const section = document.createElement('section');
    section.className = 'admin-section'; section.id = 'reviewsSection';
    section.innerHTML = '<div class="admin-section-header"><h2 class="admin-section-title">Отзывы</h2></div><div class="admin-tickets" id="reviewsList"></div>';
    $('.admin-content').append(section);
  }

  function renderReviews(overview) {
    const list = $('#reviewsList'); list.replaceChildren();
    const reviews = [...(overview.reviews || []).map(r => ({...r, source: 'orders'})), ...(overview.request_reviews || []).map(r => ({...r, source: 'service-requests'}))];
    if (!reviews.length) { const p = document.createElement('p'); p.textContent = 'Отзывов пока нет'; list.append(p); return; }
    reviews.forEach((review) => {
      const article = document.createElement('article'); article.className = 'admin-ticket';
      const title = document.createElement('h3'); title.textContent = `${review.name || 'Заказчик'} · ${review.rating}/5 · ${review.visible ? 'Опубликован' : 'На проверке'}`;
      const body = document.createElement('p'); body.textContent = review.body;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'admin-btn';
      button.textContent = review.visible ? 'Скрыть' : 'Опубликовать';
      button.onclick = async () => {
        button.disabled = true;
        try {
          const path = review.source === 'orders' ? `/api/admin/reviews/${review.id}` : `/api/admin/service-request-reviews/${review.id}`;
          await api(path, {method: 'PATCH', body: JSON.stringify({visible: !review.visible})});
          notify('Видимость отзыва изменена'); await load();
        } catch (error) { notify(error.message, 'error'); button.disabled = false; }
      };
      article.append(title, body, button); list.append(article);
    });
  }

  function renderTickets(tickets) {
    const list = $('#ticketsList'); list.replaceChildren();
    if (!tickets.length) { const p = document.createElement('p'); p.textContent = 'Обращений пока нет'; list.append(p); return; }
    tickets.forEach((ticket) => {
      const article = document.createElement('article'); article.className = 'admin-ticket';
      const title = document.createElement('h3'); title.textContent = `№${ticket.id} · ${ticket.subject}`;
      const meta = document.createElement('p'); meta.className = 'admin-ticket-meta'; meta.textContent = `${ticket.name || 'Пользователь'} · ${new Date(ticket.created_at).toLocaleString('ru-RU')} · ${ticket.status === 'closed' ? 'Закрыто' : 'Открыто'}`;
      const body = document.createElement('p'); body.textContent = ticket.body;
      article.append(title, meta, body);
      if (ticket.reply) { const reply = document.createElement('p'); reply.className = 'admin-ticket-reply'; reply.textContent = `Ответ: ${ticket.reply}`; article.append(reply); }
      if (ticket.status !== 'closed') {
        const label = document.createElement('label'); label.textContent = 'Ответ клиенту';
        const input = document.createElement('textarea'); input.className = 'form-control'; input.rows = 3; input.maxLength = 3000; input.minLength = 5; input.required = true; label.append(input);
        const button = document.createElement('button'); button.className = 'admin-btn admin-ticket-submit'; button.type = 'button'; button.textContent = 'Ответить';
        button.onclick = async () => { const reply = input.value.trim(); if (reply.length < 5) return notify('Ответ должен содержать минимум 5 символов', 'error'); button.disabled = true; try { await api(`/api/admin/tickets/${ticket.id}/reply`, { method: 'POST', body: JSON.stringify({ reply }) }); notify('Ответ отправлен'); await load(); } catch (error) { notify(error.message, 'error'); button.disabled = false; } };
        article.append(label, button);
      }
      list.append(article);
    });
  }

  function addRefreshButton() {
    const header = $('.admin-header-actions');
    if (!header || $('#adminRefreshBtn')) return;
    const button = document.createElement('button'); button.id = 'adminRefreshBtn'; button.className = 'admin-btn';
    button.innerHTML = '<i class="fas fa-sync-alt"></i><span>Обновить</span>'; button.onclick = load;
    header.prepend(button);
  }

  function renderUsers(users) {
    const body = $('#usersTableBody'); const term = ($('#userSearch')?.value || '').toLowerCase();
    const filtered = users.filter((u) => `${u.name} ${u.email}`.toLowerCase().includes(term));
    if (!filtered.length) return empty(body, 5, 'Пользователи не найдены');
    body.replaceChildren();
    filtered.forEach((user) => {
      const row = document.createElement('tr');
      const values = [user.id, user.name || 'Без имени', user.phone || '—', user.email || '—'];
      values.forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
      const action = document.createElement('td');
      if (user.role === 'admin') { action.textContent = '—'; row.append(action); body.append(row); return; }
      const button = document.createElement('button'); button.className = `action-btn ${user.active ? 'delete-btn' : 'admin-btn-action'}`;
      button.title = user.active ? 'Заблокировать' : 'Разблокировать'; button.setAttribute('aria-label', button.title); button.innerHTML = `<i class="fas ${user.active ? 'fa-user-slash' : 'fa-user-check'}"></i>`;
      button.onclick = async () => { try { await api(`/api/admin/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ active: !user.active }) }); notify(user.active ? 'Пользователь заблокирован' : 'Пользователь разблокирован'); await load(); } catch (e) { notify(e.message, 'error'); } };
      action.append(button); row.append(action); body.append(row);
    });
  }

  function renderRequests(requests, masters) {
    const body = $('#ordersTableBody'); const term = ($('#orderSearch')?.value || '').toLowerCase();
    const filtered = requests.filter((r) => `${r.contact_name} ${r.service_name} ${r.description}`.toLowerCase().includes(term));
    if (!filtered.length) return empty(body, 6, 'Заявки не найдены');
    body.replaceChildren();
    filtered.forEach((request) => {
      const row = document.createElement('tr');
      [request.id, request.contact_name || '—', request.service_name || '—', statusNames[request.status] || request.status, new Date(request.created_at).toLocaleString('ru-RU')].forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
      const action = document.createElement('td');
      if (['pending', 'assigned'].includes(request.status)) {
        const select = document.createElement('select'); select.className = 'form-control compact-control'; select.setAttribute('aria-label', 'Выбрать мастера');
        const emptyOption = new Option('Выбрать мастера', ''); select.append(emptyOption);
        const cleaning = request.catalog_category === 'cleaning';
        const eligible = masters.filter((m) => m.moderation === 'approved' && m.active && m.available && m.has_schedule && (cleaning ? m.can_cleaning : m.can_master));
        eligible.forEach((m) => select.append(new Option(`${m.name} (${m.district || 'район не указан'})`, m.user_id)));
        if (!eligible.length) select.append(new Option('Нет подходящих мастеров', ''));
        if (request.master_id) select.value = String(request.master_id);
        const button = document.createElement('button'); button.className = 'action-btn admin-btn-action'; button.title = 'Назначить мастера'; button.innerHTML = '<i class="fas fa-user-plus"></i>';
        button.setAttribute('aria-label', 'Назначить мастера'); button.onclick = async () => { if (!select.value) return notify('Выберите мастера', 'error'); try { await api(`/api/service-requests/${request.id}/action`, { method: 'POST', body: JSON.stringify({ action: 'assign', master_id: Number(select.value) }) }); notify('Мастер назначен'); await load(); } catch (e) { notify(e.message, 'error'); } };
        action.append(select, button);
      } else action.textContent = request.master_name || '—';
      row.append(action); body.append(row);
    });
  }

  function renderMasters(masters) {
    const body = $('#mastersTableBody'); const term = ($('#masterSearch')?.value || '').toLowerCase();
    const filtered = masters.filter((m) => `${m.name} ${m.title} ${m.city} ${m.district}`.toLowerCase().includes(term));
    if (!filtered.length) return empty(body, 6, 'Мастера не найдены'); body.replaceChildren();
    filtered.forEach((master) => {
      const row = document.createElement('tr');
      [master.name || '—', `${master.city || '—'} / ${master.district || '—'}`, master.price_from ? `от ${master.price_from} ₽` : '—', master.rating || '—', master.moderation === 'approved' ? 'Одобрен' : master.moderation === 'rejected' ? 'Отклонён' : 'На проверке'].forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
      const action = document.createElement('td');
      if (master.moderation === 'pending') ['approved', 'rejected'].forEach((status) => { const button = document.createElement('button'); button.className = `action-btn ${status === 'approved' ? 'admin-btn-action' : 'delete-btn'}`; button.title = status === 'approved' ? 'Одобрить' : 'Отклонить'; button.setAttribute('aria-label', button.title); button.innerHTML = `<i class="fas ${status === 'approved' ? 'fa-check' : 'fa-times'}"></i>`; button.onclick = async () => { let reason = ''; if (status === 'rejected') { reason = window.prompt('Причина отклонения (минимум 5 символов):', '') || ''; if (reason.trim().length < 5) return notify('Укажите причину минимум из 5 символов', 'error'); } try { await api(`/api/admin/masters/${master.user_id}/moderate`, { method: 'POST', body: JSON.stringify({ status, reason }) }); notify(status === 'approved' ? 'Мастер одобрен' : 'Мастер отклонён'); await load(); } catch (e) { notify(e.message, 'error'); } }; action.append(button); });
      else action.textContent = '—'; row.append(action); body.append(row);
    });
  }

  async function load() {
    try {
      const [overview, requests, tickets] = await Promise.all([api('/api/admin/overview'), api('/api/service-requests'), api('/api/tickets')]);
      $('#accessCheck').style.display = 'none'; $('#adminPanel').style.display = 'block'; $('#currentAdminName').textContent = 'Администратор';
      state = { overview, requests: requests || [], tickets: tickets || [] };
      const stats = overview.stats || {}; $('#totalUsers').textContent = stats.users ?? overview.users?.length ?? 0; $('#totalAdmins').textContent = overview.users?.filter((u) => u.role === 'admin').length ?? 0; $('#totalOrders').textContent = state.requests.length;
      renderUsers(overview.users || []); renderRequests(state.requests, overview.masters || []); renderMasters(overview.masters || []); renderTickets(state.tickets); renderReviews(overview);
    } catch (error) { $('#adminPanel').style.display = 'none'; $('#accessCheck').style.display = 'flex'; $('#accessError').textContent = error.message === 'Недостаточно прав' ? 'У вас нет прав администратора' : 'Не удалось подключиться к серверу'; $('#accessError').style.display = 'block'; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    addMasterSection(); addTicketSection(); addReviewSection(); addRefreshButton();
    $('#checkAccessBtn').onclick = load; $('#adminLogoutBtn').onclick = async () => { try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); } finally { location.href = 'account.html'; } };
    $('#userSearch').oninput = () => renderUsers(state.overview?.users || []); $('#orderSearch').oninput = () => renderRequests(state.requests, state.overview?.masters || []); $('#masterSearch').oninput = () => renderMasters(state.overview?.masters || []);
    load();
  });
})();
