/* Shared mobile navigation for internal pages; desktop markup stays untouched. */
(() => {
  const header = document.querySelector('.dp-header-inner');
  const desktopNav = header?.querySelector('.dp-nav');
  if (!header || !desktopNav) return;

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'dp-mobile-trigger';
  trigger.setAttribute('aria-label', 'Открыть меню');
  trigger.setAttribute('aria-controls', 'dp-mobile-nav');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.innerHTML = '<span></span><span></span><span></span>';

  const menu = document.createElement('dialog');
  menu.id = 'dp-mobile-nav';
  menu.className = 'dp-mobile-nav';
  menu.setAttribute('aria-label', 'Меню');
  menu.innerHTML = '<div class="dp-mobile-nav-head"><strong>Меню</strong><button type="button" autofocus aria-label="Закрыть меню">×</button></div><nav class="dp-mobile-links" aria-label="Мобильная навигация"><a href="/">Главная</a>' + desktopNav.innerHTML + '</nav>';
  header.append(trigger);
  document.body.append(menu);

  const closeButton = menu.querySelector('button');
  const close = () => menu.close();
  menu.addEventListener('close', () => {
    trigger.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('dp-mobile-open');
    trigger.focus();
  });
  const open = () => {
    menu.showModal();
    trigger.setAttribute('aria-expanded', 'true');
    document.body.classList.add('dp-mobile-open');
    closeButton.focus();
  };
  trigger.addEventListener('click', open);
  closeButton.addEventListener('click', close);
  menu.addEventListener('click', event => {
    const rect = menu.getBoundingClientRect();
    if (event.target.closest('a') || (event.target === menu && (event.clientX < rect.left || event.clientX > rect.right))) close();
  });
  matchMedia('(min-width: 960px)').addEventListener('change', event => { if (event.matches) close(); });

  // Keep the real admin tables; labels let narrow screens display each row as a card.
  const adminPanel = document.querySelector('#adminPanel');
  if (adminPanel) {
    const labelCells = () => {
      adminPanel.querySelectorAll('.table-responsive').forEach(container => {
      const headings = [...container.querySelectorAll('thead th')].map(th => th.textContent.trim());
      container.querySelector('table')?.setAttribute('role', 'table');
      container.querySelectorAll('tbody tr').forEach(row => [...row.cells].forEach((cell, index) => {
        if (cell.colSpan === 1 && headings[index]) cell.dataset.mobileLabel = headings[index];
      }));
      });
    };
    labelCells();
    new MutationObserver(labelCells).observe(adminPanel, {childList:true, subtree:true});
  }
})();
