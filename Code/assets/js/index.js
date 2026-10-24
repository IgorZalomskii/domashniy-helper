(function () {
    "use strict";

    let servicesData = { cleaning: { label: 'Клининг', items: [] }, master: { label: 'Услуги мастера', items: [] } };

    // Names identify illustrations; actual service IDs and prices always come from the API.
    const serviceImages = {
        "Генеральная уборка": "cleaning", "Поддерживающая уборка": "maintenance-cleaning",
        "Уборка после ремонта": "post-renovation", "Химчистка мягкой мебели": "upholstery",
        "Химчистка ковров": "carpet", "Уборка санузла": "bathroom", "Помывка окон": "windows",
        "Уборка кухни": "kitchen", "Помывка холодильника": "fridge", "Помывка духового шкафа": "oven",
        "Помывка люстр": "chandelier", "Помывка посуды": "dishes", "Помывка балкона": "balcony",
        "Помывка москитных сеток": "mosquito-screen", "Помывка кондиционера": "ac-cleaning",
        "Установка двери": "door", "Замена дверной ручки": "door-handle", "Ремонт дверного замка": "door-lock",
        "Мелкий ремонт мебели": "furniture-repair", "Замена светильника": "light-fixture",
        "Установка розетки": "socket", "Ремонт смесителя": "faucet", "Устранение засора": "drain",
        "Покраска стен": "painting", "Сборка мебели": "assembly", "Установка TV": "tv",
        "Установка кондиционера": "ac-installation", "Установка бытовой техники": "appliance",
        "Монтаж полок": "shelves", "Укладка ламината": "laminate"
    };
    let catalogStatus = 'loading';

    const state = {
        activeCategory: "cleaning",
        query: ""
    };

    document.addEventListener("DOMContentLoaded", async () => {
        bindMenu();
        bindServices();
        setCurrentYear();
        document.getElementById('retryServices').addEventListener('click', loadServices);
        await loadServices();
    });

    async function loadServices() {
        catalogStatus = 'loading';
        renderServices();
        try {
            const response = await fetch('/api/service-catalog');
            if (!response.ok) throw new Error('Каталог временно недоступен');
            const items = await response.json();
            servicesData = {
                cleaning: { label: 'Клининг', items: items.filter(item => item.category === 'cleaning').map(item => ({ ...item, price: item.price_label })) },
                master: { label: 'Услуги мастера', items: items.filter(item => item.category === 'master').map(item => ({ ...item, price: item.price_label })) }
            };
            catalogStatus = 'ready';
            document.getElementById('catalogTotal').textContent = formatServiceCount(items.length);
            bindQuickServices();
        } catch (error) {
            catalogStatus = 'error';
        }
        renderServices();
    }

    function bindMenu() {
        const mobileMenu = document.getElementById("mobileMenu");
        const openMenuBtn = document.getElementById("openMenu");
        const closeMenuBtn = document.getElementById("closeMenu");
        const menuOverlay = document.getElementById("menuOverlay");

        if (!mobileMenu || !openMenuBtn || !closeMenuBtn || !menuOverlay) {
            return;
        }

        const openMenu = () => {
            mobileMenu.classList.add("is-open");
            mobileMenu.setAttribute("aria-hidden", "false");
            mobileMenu.inert = false;
            openMenuBtn.setAttribute("aria-expanded", "true");
            menuOverlay.hidden = false;
            document.body.classList.add("menu-open");
            closeMenuBtn.focus();
        };

        const closeMenu = () => {
            mobileMenu.classList.remove("is-open");
            mobileMenu.setAttribute("aria-hidden", "true");
            mobileMenu.inert = true;
            openMenuBtn.setAttribute("aria-expanded", "false");
            menuOverlay.hidden = true;
            document.body.classList.remove("menu-open");
            openMenuBtn.focus();
        };

        openMenuBtn.addEventListener("click", openMenu);
        closeMenuBtn.addEventListener("click", closeMenu);
        menuOverlay.addEventListener("click", closeMenu);
        window.matchMedia('(min-width: 1061px)').addEventListener('change', (event) => {
            if (event.matches && mobileMenu.classList.contains('is-open')) closeMenu();
        });
        mobileMenu.addEventListener("click", (event) => {
            if (event.target.closest("a")) {
                closeMenu();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === 'Tab' && mobileMenu.classList.contains('is-open')) {
                const links = mobileMenu.querySelectorAll('a, button');
                const first = links[0], last = links[links.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault(); last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault(); first.focus();
                }
            }
            if (event.key === "Escape" && mobileMenu.classList.contains("is-open")) {
                closeMenu();
            }
        });
    }

    function bindServices() {
        const tabs = document.querySelectorAll("[data-service-tab]");
        const searchInput = document.getElementById("serviceSearch");

        tabs.forEach((tab) => {
            tab.addEventListener("click", () => {
                const nextCategory = tab.dataset.serviceTab;

                if (!servicesData[nextCategory]) {
                    return;
                }

                state.activeCategory = nextCategory;
                tabs.forEach((item) => {
                    const isActive = item === tab;

                    item.classList.toggle("is-active", isActive);
                    item.setAttribute("aria-selected", String(isActive));
                    item.tabIndex = isActive ? 0 : -1;
                });
                renderServices();
            });
            tab.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const list = [...tabs], current = list.indexOf(tab);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 :
                    (current + (event.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length;
                list[next].click();
                list[next].focus();
            });
        });

        if (searchInput) {
            searchInput.addEventListener("input", () => {
                state.query = searchInput.value.trim().toLowerCase();
                renderServices();
            });
        }
    }

    function bindQuickServices() {
        document.querySelectorAll("[data-service-preset]").forEach((link) => {
            const service = findServiceByName(link.dataset.servicePreset);
            if (!service) return;
            link.href = 'application?service=' + service.id;
            link.querySelector('.featured-price').textContent = service.price.replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1\u00a0').replace('руб.', '₽');
        });
    }

    function renderServices() {
        const serviceCards = document.getElementById("serviceCards");
        const emptyState = document.getElementById("emptyState");
        const serviceCount = document.getElementById("serviceCount");
        const activeCategory = document.getElementById("activeCategory");
        const category = servicesData[state.activeCategory];

        if (!serviceCards || !emptyState || !serviceCount || !activeCategory || !category) {
            return;
        }

        const services = filterServices(category.items);

        serviceCards.replaceChildren();
        if (catalogStatus === 'loading') {
            for (let i = 0; i < 15; i++) {
                const skeleton = document.createElement('div');
                skeleton.className = 'service-card catalog-skeleton';
                skeleton.setAttribute('aria-hidden', 'true');
                skeleton.innerHTML = '<div class="service-card__media"></div><div class="service-card__copy"></div>';
                serviceCards.append(skeleton);
            }
        }
        if (catalogStatus === 'ready') services.forEach((service) => serviceCards.append(createServiceCard(service)));
        const panel = document.getElementById('catalogPanel');
        panel.setAttribute('aria-busy', String(catalogStatus === 'loading'));
        panel.setAttribute('aria-labelledby', 'tab-' + state.activeCategory);
        document.getElementById('retryServices').hidden = catalogStatus !== 'error';
        emptyState.hidden = catalogStatus === 'loading' || (catalogStatus === 'ready' && services.length > 0);
        emptyState.textContent = catalogStatus === 'loading' ? 'Загружаем каталог услуг…' :
            catalogStatus === 'error' ? 'Не удалось загрузить каталог. Проверьте подключение и попробуйте снова.' :
            'По такому запросу услуг не найдено. Попробуйте другое название или категорию.';
        serviceCount.textContent = catalogStatus === 'ready' ? formatServiceCount(services.length) :
            catalogStatus === 'loading' ? 'Загружаем услуги…' : 'Каталог недоступен';
        activeCategory.textContent = category.label;
    }

    function filterServices(items) {
        if (!state.query) {
            return items;
        }

        return items.filter((service) => {
            const haystack = `${service.name} ${service.description} ${service.price}`.toLowerCase();

            return haystack.includes(state.query);
        });
    }

    function createServiceCard(service) {
        const card = document.createElement("a");
        const media = document.createElement("div");
        const copy = document.createElement("div");
        const name = document.createElement("h3");
        const price = document.createElement("span");
        const description = document.createElement("p");
        const action = document.createElement("span");

        card.className = "service-card";
        card.href = 'application?service=' + encodeURIComponent(service.id);
        card.setAttribute('aria-label', service.name + ', ' + service.price + '. Оставить заявку');
        media.className = 'service-card__media';
        media.setAttribute('aria-hidden', 'true');
        const slug = serviceImages[service.name];
        if (slug) {
            const photo = document.createElement('img');
            photo.src = 'assets/images/home/' + slug + '-v1.webp';
            photo.width = 720; photo.height = 480;
            photo.alt = ''; photo.loading = 'lazy'; photo.decoding = 'async';
            photo.addEventListener('error', () => { photo.remove(); media.classList.add('is-unavailable'); }, { once: true });
            media.append(photo);
        } else media.classList.add('is-unavailable');
        copy.className = 'service-card__copy';
        name.className = 'service-card__title';
        name.textContent = service.name;
        price.className = "service-card__price";
        price.textContent = service.price.replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1\u00a0').replace('руб.', '₽');
        description.className = "service-card__description";
        description.textContent = service.description;
        action.className = "service-card__arrow";
        action.setAttribute('aria-hidden', 'true');
        action.textContent = '→';
        copy.append(name, description, price, action);
        card.append(media, copy);

        return card;
    }

    function findServiceByName(name) {
        return Object.values(servicesData)
            .flatMap((category) => category.items)
            .find((service) => service.name === name);
    }

    function showToast(message, type) {
        const toast = document.getElementById("toast");

        if (!toast) {
            return;
        }

        toast.textContent = message;
        toast.className = `toast${type ? ` is-${type}` : ""}`;
        toast.hidden = false;
        window.clearTimeout(showToast.timer);
        showToast.timer = window.setTimeout(() => {
            toast.hidden = true;
        }, 4200);
    }

    function setCurrentYear() {
        const year = document.getElementById("currentYear");

        if (year) {
            year.textContent = String(new Date().getFullYear());
        }
    }

    function formatServiceCount(count) {
        const lastTwoDigits = count % 100;
        const lastDigit = count % 10;

        if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
            return `${count} услуг`;
        }

        if (lastDigit === 1) {
            return `${count} услуга`;
        }

        if (lastDigit >= 2 && lastDigit <= 4) {
            return `${count} услуги`;
        }

        return `${count} услуг`;
    }
})();
