/* Progressive enhancement only. The server owns validation and stored totals. */
(() => {
  'use strict';
  const THEME_KEY = 'biznova.theme';
  const themes = ['light', 'dark', 'system'];
  const root = document.documentElement;
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

  function readTheme() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      return themes.includes(saved) ? saved : 'dark';
    } catch (_) { return root.dataset.themePreference || 'dark'; }
  }
  function applyTheme(preference, persist = false) {
    if (!themes.includes(preference)) preference = 'system';
    root.dataset.themePreference = preference;
    root.dataset.theme = preference === 'system' ? (systemTheme.matches ? 'dark' : 'light') : preference;
    $$('[data-theme-select]').forEach(select => { select.value = preference; });
    $$('[data-theme-value]').forEach(input => { input.value = preference; });
    if (persist) {
      try { localStorage.setItem(THEME_KEY, preference); }
      catch (_) { notify('Your theme is applied for this page, but this browser could not save the preference.', 'info'); }
    }
  }
  function notify(message, type = 'error', scope = null) {
    // Keep errors inside the active modal so they are not hidden by its inert backdrop.
    const modal = scope && scope.closest('dialog[open]');
    let region = modal ? $('[data-local-notifications]', modal) : $('#notifications');
    if (!region && modal) {
      region = document.createElement('div');
      region.dataset.localNotifications = '';
      region.setAttribute('aria-live', 'polite');
      modal.insertBefore(region, $('form', modal));
    }
    if (!region) return;
    const notice = document.createElement('div');
    notice.className = `notice notice-${type}`;
    notice.setAttribute('role', type === 'error' ? 'alert' : 'status');
    notice.tabIndex = -1;
    const text = document.createElement('p');
    text.textContent = message;
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'icon-button';
    dismiss.setAttribute('aria-label', 'Dismiss notification');
    dismiss.textContent = '×';
    dismiss.addEventListener('click', () => {
      notice.remove();
      if (scope) $('button[type="submit"]', scope)?.focus();
    });
    notice.append(text, dismiss);
    region.replaceChildren(notice);
    notice.focus({ preventScroll: true });
    notice.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }

  function setupDrawer() {
    const sidebar = $('#sidebar');
    if (!sidebar) return;
    const shell = $('#app-shell');
    const backdrop = $('[data-drawer-backdrop]');
    const openButton = $('[data-open-drawer]');
    const small = window.matchMedia('(max-width: 767px)');
    let returnFocus = null;
    const focusable = () => $$('a[href], button:not(:disabled), select, input:not([type="hidden"]), [tabindex="0"]', sidebar).filter(el => el.getClientRects().length);
    function close(restore = true) {
      sidebar.classList.remove('is-open');
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      sidebar.inert = small.matches;
      shell.inert = false;
      backdrop.hidden = true;
      document.body.classList.remove('drawer-open');
      openButton?.setAttribute('aria-expanded', 'false');
      if (restore && returnFocus?.isConnected) returnFocus.focus();
    }
    function open() {
      if (!small.matches) return;
      returnFocus = document.activeElement;
      sidebar.inert = false;
      sidebar.classList.add('is-open');
      sidebar.setAttribute('role', 'dialog');
      sidebar.setAttribute('aria-modal', 'true');
      shell.inert = true;
      backdrop.hidden = false;
      document.body.classList.add('drawer-open');
      openButton?.setAttribute('aria-expanded', 'true');
      $('[data-close-drawer]', sidebar)?.focus();
    }
    openButton?.addEventListener('click', open);
    $('[data-close-drawer]')?.addEventListener('click', () => close());
    backdrop.addEventListener('click', () => close());
    sidebar.addEventListener('click', event => {
      if (event.target.closest('a[href]') && small.matches) close();
    });
    document.addEventListener('keydown', event => {
      if (!sidebar.classList.contains('is-open')) return;
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        const nodes = focusable();
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
          event.preventDefault(); first?.focus();
        }
      }
    });
    small.addEventListener('change', () => close(false));
    close(false);
  }

  function setupDialogs() {
    const returnFocus = new WeakMap();
    function open(dialog, trigger = document.activeElement) {
      if (!dialog || typeof dialog.showModal !== 'function') return;
      if (dialog.open) dialog.close();
      returnFocus.set(dialog, trigger);
      dialog.showModal();
      const target = $('[aria-invalid="true"], textarea, input:not([type="hidden"]), [data-close-dialog]', dialog);
      target?.focus();
    }
    $$('[data-modal]').forEach(dialog => {
      if (typeof dialog.showModal !== 'function') return; // The visible, native form still works.
      const reopen = dialog.hasAttribute('data-reopen');
      dialog.close();
      dialog.addEventListener('close', () => {
        const trigger = returnFocus.get(dialog);
        if (trigger?.isConnected) trigger.focus({ preventScroll: true });
      });
      $$('[data-close-dialog]', dialog).forEach(button => button.addEventListener('click', () => dialog.close()));
      // Escape uses the native cancel event; Tab is wrapped so focus cannot leave to the page or browser chrome.
      dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const nodes = $$('a[href], button:not(:disabled), select:not(:disabled), textarea:not(:disabled), input:not([type="hidden"]):not(:disabled)', dialog).filter(el => el.getClientRects().length);
        if (!nodes.length) return;
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
      if (reopen) {
        const trigger = $$('[data-open-dialog]').find(el => el.dataset.openDialog === dialog.id);
        open(dialog, trigger || $('#main-content'));
      }
    });
    $$('[data-open-dialog]').forEach(trigger => trigger.addEventListener('click', event => {
      const dialog = document.getElementById(trigger.dataset.openDialog);
      if (dialog && typeof dialog.showModal === 'function') {
        event.preventDefault(); open(dialog, trigger);
      }
    }));
    $$('[data-error-summary] a[href^="#"]').forEach(link => link.addEventListener('click', event => {
      const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      if (!target) return;
      event.preventDefault();
      const dialog = target.closest('dialog');
      if (dialog && !dialog.open) open(dialog, link);
      target.focus();
      target.scrollIntoView({ block: 'center' });
    }));
  }

  function setupPeriods() {
    $$('[data-period]').forEach(select => {
      const dates = $('[data-custom-dates]', select.form);
      if (!dates) return;
      function update() {
        const custom = select.value === 'custom';
        dates.hidden = !custom;
        $$('input', dates).forEach(input => { input.disabled = !custom; input.required = custom; });
      }
      select.addEventListener('change', update);
      update();
    });
  }

  // No binary floating-point operations are used to parse, multiply, sum, or format money.
  function paise(raw) {
    if (!/^[0-9]{1,9}(?:\.[0-9]{1,2})?$/.test(raw)) return null;
    const [whole, fraction = ''] = raw.split('.');
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  }
  function wholeQuantity(raw) {
    if (!/^[0-9]{1,19}$/.test(raw)) return null;
    const value = BigInt(raw);
    return value >= 1n && value <= 1000000n ? value : null;
  }
  function formatMoney(value) {
    const currency = document.body.dataset.currency || 'INR';
    const symbols = { INR: '₹', USD: '$', EUR: '€', GBP: '£' };
    const negative = value < 0n;
    if (negative) value = -value;
    let digits = (value / 100n).toString();
    if (currency === 'INR' && digits.length > 3) {
      const last = digits.slice(-3);
      digits = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last;
    } else {
      digits = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }
    return `${negative ? '−' : ''}${symbols[currency] || currency + ' '}${digits}.${(value % 100n).toString().padStart(2, '0')}`;
  }

  function setupItems() {
    const form = $('[data-transaction-form]');
    if (!form) return;
    const container = $('[data-item-rows]', form);
    const template = $('#item-row-template');
    const announcement = $('[data-items-announcement]', form);
    const sale = form.dataset.kind === 'sale';
    const priceKey = sale ? 'price' : 'cost';
    const field = (row, name) => $(`[data-item-field="${name}"]`, row);
    const rows = () => $$('[data-item-row]', container);
    const add = $('[data-add-row]', form);
    function reindex() {
      const list = rows();
      list.forEach((row, index) => {
        const oldPrefix = field(row, 'product_id').id.replace(/product_id$/, '');
        const newPrefix = `items-${index}-`;
        $$('*', row).forEach(el => {
          for (const name of ['id', 'for', 'aria-describedby', 'aria-controls']) {
            if (el.hasAttribute(name)) el.setAttribute(name, el.getAttribute(name).split(oldPrefix).join(newPrefix));
          }
        });
        $$('[data-item-field]', row).forEach(input => { input.name = `items[${index}][${input.dataset.itemField}]`; });
        $('[data-row-number]', row).textContent = String(index + 1);
        const remove = $('[data-remove-row]', row);
        remove.disabled = list.length === 1;
        remove.setAttribute('aria-label', `Remove item ${index + 1}`);
      });
      if (add) add.disabled = list.length >= 100 || !$$('option[data-stock]', list[0] || template.content).length;
    }
    function filterProducts(row) {
      const term = $('[data-product-search]', row).value.trim().toLocaleLowerCase();
      const select = field(row, 'product_id');
      let matches = 0;
      [...select.options].forEach(option => {
        const match = !option.value || option.textContent.toLocaleLowerCase().includes(term);
        const keep = match || option.selected;
        option.hidden = !keep;
        option.disabled = !keep;
        if (match && option.value) matches += 1;
      });
      $('[data-search-status]', row).textContent = `${matches} matching products. Your selected product remains available in the list.`;
    }
    function calculate() {
      const list = rows();
      const counts = new Map();
      list.forEach(row => {
        const id = field(row, 'product_id').value;
        if (id) counts.set(id, (counts.get(id) || 0) + 1);
      });
      let total = 0n;
      let complete = list.length > 0;
      list.forEach(row => {
        const select = field(row, 'product_id');
        const quantityInput = field(row, 'quantity');
        const priceInput = field(row, priceKey);
        const option = select.selectedOptions[0];
        const quantity = wholeQuantity(quantityInput.value.trim());
        const raw = priceInput.value.trim();
        const price = paise(raw || option?.dataset.price || '');
        const duplicate = select.value && counts.get(select.value) > 1;
        const unavailable = !!option?.dataset.unavailable;
        const messages = [];
        if (duplicate) messages.push('This product is already on another line. Combine its quantities into one line.');
        if (unavailable) messages.push('This product is unavailable. Choose an active product.');
        select.setCustomValidity(duplicate ? 'Combine duplicate products into one line.' : unavailable ? 'Choose an active product.' : '');
        const badQuantity = quantityInput.value !== '' && quantity === null;
        const badPrice = raw !== '' && price === null;
        quantityInput.setCustomValidity(badQuantity ? 'Use a whole quantity from 1 to 1,000,000.' : '');
        priceInput.setCustomValidity(badPrice ? 'Use up to 9 digits and 2 decimal places, without separators or currency symbols.' : '');
        if (badQuantity) messages.push('Quantity must be a whole number from 1 to 1,000,000.');
        if (badPrice) messages.push('Enter a unit amount with up to 9 digits and 2 decimal places.');
        const hint = $('[data-stock-hint]', row);
        hint.textContent = option?.dataset.stock !== undefined ? `${option.dataset.stock} ${option.dataset.unit || 'units'} currently available. Stock is checked again on save.` : 'Availability is checked again when you save.';
        if (sale && quantity !== null && /^[0-9]+$/.test(option?.dataset.stock || '') && quantity > BigInt(option.dataset.stock)) {
          messages.push('Quantity exceeds the displayed stock. The server will check the latest availability when you save.');
        }
        const error = $('[data-row-error]', row);
        error.hidden = messages.length === 0;
        error.textContent = messages.join(' ');
        [quantityInput, priceInput].forEach(input => {
          const descriptions = (input.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
          if (!descriptions.includes(error.id)) descriptions.push(error.id);
          input.setAttribute('aria-describedby', descriptions.join(' '));
        });
        const valid = select.value && !duplicate && !unavailable && quantity !== null && price !== null;
        $('[data-line-total]', row).textContent = valid ? formatMoney(quantity * price) : '—';
        if (valid) total += quantity * price;
        else complete = false;
      });
      const discountInput = $('[name="discount"]', form);
      const discount = discountInput ? paise(discountInput.value.trim() || '0') : 0n;
      const tooLarge = complete && discount !== null && discount > total;
      if (discountInput) {
        const error = $('[data-discount-error]', form);
        const message = discount === null ? 'Use up to 9 digits and 2 decimal places for the discount.' : tooLarge ? 'Discount cannot exceed the subtotal.' : '';
        discountInput.setCustomValidity(message);
        error.textContent = message;
        error.hidden = !message;
        error.id = 'discount-preview-error';
        const descriptions = new Set((discountInput.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
        descriptions.add(error.id);
        discountInput.setAttribute('aria-describedby', [...descriptions].join(' '));
      }
      const subtotal = $('[data-subtotal]', form);
      if (subtotal) subtotal.textContent = complete ? formatMoney(total) : '—';
      const discountTotal = $('[data-discount-total]', form);
      if (discountTotal) discountTotal.textContent = discount !== null ? formatMoney(discount) : '—';
      $('[data-grand-total]', form).textContent = complete && discount !== null && !tooLarge ? formatMoney(total - discount) : '—';
    }
    form.addEventListener('input', event => {
      const row = event.target.closest('[data-item-row]');
      if (event.target.matches('[data-product-search]')) filterProducts(row);
      else if (event.target.matches('[data-item-field], [name="discount"]')) calculate();
    });
    form.addEventListener('change', event => {
      if (!event.target.matches('[data-item-field="product_id"]')) return;
      const row = event.target.closest('[data-item-row]');
      const selected = event.target.selectedOptions[0];
      // Only deliberate selection changes replace the price; initial and returned inputs are untouched.
      if (selected?.dataset.price !== undefined) field(row, priceKey).value = selected.dataset.price;
      filterProducts(row);
      calculate();
    });
    add?.addEventListener('click', () => {
      if (rows().length >= 100) return;
      container.append(template.content.cloneNode(true));
      reindex(); calculate();
      const row = rows().at(-1);
      $('[data-product-search]', row).focus();
      announcement.textContent = `Item ${rows().length} added.`;
    });
    container.addEventListener('click', event => {
      const button = event.target.closest('[data-remove-row]');
      if (!button || rows().length <= 1) return;
      const list = rows();
      const row = button.closest('[data-item-row]');
      const index = list.indexOf(row);
      row.remove();
      reindex(); calculate();
      field(rows()[Math.min(index, rows().length - 1)], 'product_id').focus();
      announcement.textContent = `Item ${index + 1} removed. ${rows().length} items remaining.`;
    });
    form.addEventListener('submit', event => {
      calculate();
      if (!form.checkValidity()) { event.preventDefault(); form.reportValidity(); }
    });
    reindex();
    calculate();
  }

  function setupAsyncForms() {
    let activeRequests = 0;
    const bar = $('#loading-bar');
    $$('form[data-async]').forEach(form => {
      if (form.hasAttribute('data-sensitive-form') || $('[type="password"]', form)) return;
      form.addEventListener('submit', async event => {
        if (event.defaultPrevented || form.method.toLowerCase() !== 'post' || !window.fetch || !window.AbortController) return;
        event.preventDefault();
        if (form.dataset.submitting === 'true') return;
        if (!form.checkValidity()) { form.reportValidity(); return; }
        const action = new URL(form.action, location.href);
        if (action.origin !== location.origin) return;
        const body = new FormData(form);
        if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
        const retryKey = $('[name="idempotency_key"]', form)?.value;
        const buttons = $$('button[type="submit"], input[type="submit"]', form);
        const originalStates = buttons.map(button => ({ button, disabled: button.disabled, html: button.innerHTML }));
        const submitter = event.submitter;
        form.dataset.submitting = 'true';
        form.setAttribute('aria-busy', 'true');
        buttons.forEach(button => { button.disabled = true; });
        if (submitter?.tagName === 'BUTTON') {
          submitter.setAttribute('aria-busy', 'true');
          submitter.textContent = submitter.dataset.loadingLabel || 'Saving…';
        }
        activeRequests += 1;
        if (bar) bar.hidden = false;
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 20000);
        try {
          const response = await fetch(action.href, {
            method: 'POST', body, credentials: 'same-origin',
            headers: { 'Accept': 'text/html', 'X-Requested-With': 'XMLHttpRequest' },
            signal: controller.signal, redirect: 'follow'
          });
          if (response.redirected && response.ok) {
            const destination = new URL(response.url);
            if (destination.origin !== location.origin) throw new Error('Unexpected redirect.');
            if (destination.pathname === '/auth/login') {
              notify('Your session has ended. Sign in in another tab, then retry this form. Your input and request key have been kept.', 'error', form);
              return;
            }
            location.assign(destination.href);
            return;
          }
          if ([400, 401, 403, 419].includes(response.status)) {
            notify('The request could not be verified or your session has expired. Your input and request key are unchanged. Sign in in another tab, then retry; if the security token remains expired, copy your input before refreshing this page.', 'error', form);
            return;
          }
          if (response.status >= 500) {
            notify('The server could not finish the request. Your input has been kept. Check your connection and retry when the service is available. Transaction retries keep the same request key.', 'error', form);
            return;
          }
          if (response.status === 422 || response.status === 409) {
            const html = await response.text();
            if (!(response.headers.get('content-type') || '').includes('text/html')) throw new Error('Expected an HTML form response.');
            const returned = new DOMParser().parseFromString(html, 'text/html');
            // A generic error page must not replace an editable form or discard its retry key.
            const replacement = [...returned.forms].find(candidate => {
              const target = new URL(candidate.getAttribute('action') || action.href, action.href);
              return target.origin === action.origin && target.pathname === action.pathname;
            });
            if (!replacement || !returned.querySelector('main')) {
              notify('This request conflicts with the latest records. Your input is still here. Review the record in another tab before trying again.', 'error', form);
              return;
            }
            const csrfError = returned.querySelector('#csrf_token-error');
            if (csrfError) {
              notify('The security token has expired. Your input and request key are unchanged. Copy your input before refreshing the form.', 'error', form);
              return;
            }
            if (retryKey !== undefined) {
              const keyInput = replacement.querySelector('[name="idempotency_key"]');
              if (!keyInput) throw new Error('The returned form is missing its retry key.');
              keyInput.setAttribute('value', retryKey);
            }
            // A complete document replacement reruns the head prepaint script and deferred initializer.
            const serialized = '<!doctype html>\n' + returned.documentElement.outerHTML;
            document.open();
            document.write(serialized);
            document.close();
            return;
          }
          notify('The server returned an unexpected response. Your input is unchanged. Check the ledger in another tab before retrying.', 'error', form);
        } catch (error) {
          const message = error.name === 'AbortError'
            ? 'The request took longer than 20 seconds. Your input is still here. The server may have saved it; check the ledger or retry this transaction with its unchanged request key.'
            : 'The connection was interrupted or the response could not be read. Your input and request key are unchanged. Check the ledger before retrying; transaction retries use the same key.';
          notify(message, 'error', form);
        } finally {
          window.clearTimeout(timer);
          activeRequests -= 1;
          if (bar?.isConnected) bar.hidden = activeRequests === 0;
          if (form.isConnected) {
            delete form.dataset.submitting;
            form.removeAttribute('aria-busy');
            originalStates.forEach(({ button, disabled, html }) => {
              button.disabled = disabled;
              button.innerHTML = html;
              button.removeAttribute('aria-busy');
            });
          }
        }
      });
    });
  }

  function init() {
    if (root.dataset.initialized === 'true') return;
    root.dataset.initialized = 'true';
    applyTheme(readTheme());
    $$('[data-theme-select]').forEach(select => select.addEventListener('change', () => applyTheme(select.value, true)));
    systemTheme.addEventListener('change', () => {
      if (root.dataset.themePreference === 'system') applyTheme('system');
    });
    window.addEventListener('storage', event => {
      if (event.key === THEME_KEY || event.key === null) applyTheme(readTheme());
    });
    setupDrawer();
    setupDialogs();
    setupPeriods();
    setupItems();
    setupAsyncForms();
    $$('[data-dismiss-notice]').forEach(button => button.addEventListener('click', () => {
      button.closest('.notice').remove();
      $('#main-content')?.focus({ preventScroll: true });
    }));
    $$('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
    $$('[data-go-back]').forEach(button => button.addEventListener('click', () => {
      if (history.length > 1) history.back();
      else location.assign('/');
    }));
    if (!$('dialog[open]')) $('[data-error-summary]')?.focus();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
