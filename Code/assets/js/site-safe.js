/* Локальная логика вторичных страниц. Не зависит от Firebase и внешних аккаунтов. */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.faq-question').forEach(question => {
            question.setAttribute('role', 'button');
            question.setAttribute('tabindex', '0');
            const toggle = () => {
                const item = question.closest('.faq-item');
                item?.classList.toggle('active');
                question.setAttribute('aria-expanded', String(item?.classList.contains('active')));
            };
            question.addEventListener('click', toggle);
            question.addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
            });
        });

        document.querySelectorAll('.accordion-header').forEach(header => {
            header.setAttribute('role', 'button');
            header.setAttribute('tabindex', '0');
            const toggle = () => {
                header.classList.toggle('active');
                header.nextElementSibling?.classList.toggle('active');
            };
            header.addEventListener('click', toggle);
            header.addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
            });
        });

        const referralLink = document.getElementById('referralLink');
        if (referralLink) {
            referralLink.value = 'Реферальная ссылка станет доступна после запуска программы';
            document.querySelectorAll('#copyBtn, #shareWhatsApp, #shareTelegram, #shareVK').forEach(button => {
                button.disabled = true;
                button.title = 'Функция пока недоступна';
                button.addEventListener('click', () => window.alert('Реферальная программа ещё не подключена.'));
            });
        }

        document.querySelectorAll('[data-support-link]').forEach(link => {
            link.addEventListener('click', function (e) {
                if (this.getAttribute('href') === '/support') {
                    e.preventDefault();
                    window.location.href = '/support';
                }
            });
        });

        const slides = [...document.querySelectorAll('.slide-image')];
        const modal = document.getElementById('slideModal');
        const modalImage = document.getElementById('modalImage');
        let current = 0;
        let slideOpener = null;
        const show = index => {
            if (!modal || !modalImage || !slides.length) return;
            current = (index + slides.length) % slides.length;
            modalImage.src = slides[current].dataset.slideSrc || slides[current].src;
            if (!modal.open) { slideOpener = document.activeElement; modal.showModal(); }
            modal.querySelector('[data-close-modal]').focus();
            document.body.style.overflow = 'hidden';
        };
        const hide = () => { if (modal?.open) modal.close(); document.body.style.overflow = ''; slideOpener?.focus(); };
        slides.forEach((slide, index) => {
            slide.tabIndex = 0; slide.setAttribute('role', 'button');
            slide.setAttribute('aria-label', 'Открыть: ' + slide.alt);
            slide.addEventListener('click', () => show(index));
            slide.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(index); } });
        });
        document.querySelector('[data-close-modal]')?.addEventListener('click', hide);
        document.querySelectorAll('[data-slide-direction]').forEach(button => button.addEventListener('click', () => show(current + Number(button.dataset.slideDirection))));
        modal?.addEventListener('click', e => { if (e.target === modal) hide(); });
        modal?.addEventListener('cancel', e => { e.preventDefault(); hide(); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
    });
})();
