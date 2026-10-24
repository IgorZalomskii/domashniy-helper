(() => {
  'use strict';
  const form = document.querySelector('#masterRequestForm');
  const modal = document.querySelector('#serviceModal');
  const table = document.querySelector('#servicesTableBody');
  const cards = document.querySelector('#serviceCards');
  const selectedBox = document.querySelector('#selectedService');
  const dateInput = document.querySelector('#dateTimePicker');
  const datePicker = document.querySelector('#datePicker');
  const submit = document.querySelector('#submitBtn');
  let services = [];
  let category = 'cleaning';
  let selected = null;
  let requestKey = crypto.randomUUID();
  let modalOpener = null;

  function message(text, kind = 'error') {
    let node = document.querySelector('#requestMessage');
    if (!node) {
      node = document.createElement('p');
      node.id = 'requestMessage';
      node.tabIndex = -1;
      node.setAttribute('role', 'status');
      form.before(node);
    }
    node.className = `request-message request-message--${kind}`;
    node.textContent = text;
  }

  async function json(url, options = {}) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    return body;
  }

  function openModal() {
    modalOpener = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => document.querySelector('#closeServiceModal').focus());
  }

  function closeModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    (modalOpener?.isConnected ? modalOpener : document.querySelector('#openServiceModal')).focus();
  }

  function choose(service, close = true) {
    selected = service;
    document.querySelector('#selectedServiceName').textContent = service.name;
    document.querySelector('#selectedServicePrice').textContent = service.price_label;
    selectedBox.classList.add('active');
    document.querySelector('#openServiceModal').textContent = 'Изменить услугу';
    if (close) closeModal();
  }

  function render() {
    table.replaceChildren();
    cards.replaceChildren();
    for (const service of services.filter(item => item.category === category)) {
      const row = document.createElement('tr');
      for (const value of [service.name, service.description, service.price_label]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      }
      const cell = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'service-select-btn';
      button.textContent = 'Выбрать';
      button.onclick = () => choose(service);
      cell.append(button);
      row.append(cell);
      table.append(row);
      const card = document.createElement('article');
      card.className = 'service-card';
      const title = document.createElement('h4');
      title.textContent = service.name;
      const desc = document.createElement('p');
      desc.textContent = service.description;
      const price = document.createElement('strong');
      price.textContent = service.price_label;
      const action = button.cloneNode(true);
      action.onclick = () => choose(service);
      card.append(title, desc, price, action);
      cards.append(card);
    }
  }

  document.querySelector('#openServiceModal').onclick = openModal;
  document.querySelector('#closeServiceModal').onclick = closeModal;
  modal.addEventListener('click', event => { if (event.target === modal) closeModal(); });
  document.addEventListener('keydown', event => {
    if (!modal.classList.contains('open')) return;
    if (event.key === 'Escape') { closeModal(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll('button, a[href], input, select, textarea')].filter(node => !node.disabled && node.getClientRects().length);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  for (const [id, value] of [['cleaningBtn', 'cleaning'], ['masterBtn', 'master']]) {
    document.getElementById(id).onclick = () => {
      category = value;
      document.querySelectorAll('.service-toggle-btn').forEach(button => button.classList.toggle('active', button.id === id));
      render();
    };
  }
  document.querySelectorAll('.order-type-btn').forEach(button => button.onclick = () => {
    const scheduled = button.dataset.type === 'date';
    document.querySelectorAll('.order-type-btn').forEach(other => other.classList.toggle('active', other === button));
    datePicker.classList.toggle('active', scheduled);
    dateInput.required = scheduled;
    if (!scheduled) dateInput.value = '';
  });

  form.onsubmit = async event => {
    event.preventDefault();
    if (!selected) { message('Сначала выберите услугу.'); openModal(); return; }
    if (!form.reportValidity()) return;
    submit.disabled = true;
    const data = new FormData(form);
    try {
      const result = await json('/api/service-requests', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'home-helper' },
        body: JSON.stringify({ catalog_service_id: selected.id, contact_name: `${data.get('firstName')} ${data.get('lastName')}`.trim(), phone: data.get('whatsapp'), address: data.get('address'), description: data.get('problem'), desired_at: dateInput.value ? new Date(dateInput.value).toISOString() : null, request_key: requestKey })
      });
      requestKey = crypto.randomUUID();
      form.reset();
      datePicker.classList.remove('active');
      dateInput.required = false;
      document.querySelectorAll('.order-type-btn').forEach(button => button.classList.toggle('active', button.dataset.type === 'online'));
      selected = null;
      selectedBox.classList.remove('active');
      document.querySelector('#openServiceModal').textContent = 'Выберите услугу';
      message(`Заявка №${result.id} сохранена. Статус можно посмотреть в личном кабинете.`, 'success');
      document.querySelector('#requestMessage').focus();
    } catch (error) { message(error.message); }
    finally { submit.disabled = false; }
  };

  Promise.all([json('/api/auth/me').catch(() => null), json('/api/service-catalog')]).then(([user, catalog]) => {
    services = catalog;
    render();
    const id = Number(new URLSearchParams(location.search).get('service'));
    const preselected = services.find(item => item.id === id);
    if (preselected) choose(preselected, false);
    if (!user || user.role !== 'customer') {
      form.hidden = true;
      const link = document.createElement('a');
      link.className = 'submit-btn';
      link.href = '/account?next=' + encodeURIComponent(location.pathname + location.search);
      link.textContent = user ? 'Войти как заказчик' : 'Войти, чтобы оставить заявку';
      form.before(link);
      message('Заявка сохраняется в личном кабинете. Для отправки войдите или зарегистрируйтесь как заказчик.', 'info');
    }
  }).catch(error => { form.hidden = true; message(error.message); });
})();
