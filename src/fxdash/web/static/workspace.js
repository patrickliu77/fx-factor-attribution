// Progressive visual enhancements. Saved data and application state stay in app.js.
const view = document.getElementById('view');
const skip = document.querySelector('.skip-link');
skip?.addEventListener('click', event => {
  if (!view) return;
  event.preventDefault();
  view.focus();
  view.scrollIntoView({block: 'start', behavior: 'instant'});
});

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
if (view && 'IntersectionObserver' in window) {
  const pending = new Set();
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add('workspace-revealed');
      pending.delete(entry.target);
      observer.unobserve(entry.target);
    }
  }, {threshold: 0.05, rootMargin: '0px 0px -24px 0px'});
  const scan = () => {
    // Discard removed route elements so observers never retain a prior page.
    for (const element of pending) {
      if (element.isConnected) continue;
      observer.unobserve(element);
      pending.delete(element);
    }
    if (reduced.matches) return;
    for (const element of view.querySelectorAll('.card, .mini, .brief-board, .structure, .method__detail')) {
      if (element.dataset.workspaceSeen) continue;
      if (element.closest('[hidden], details:not([open])')) continue;
      element.dataset.workspaceSeen = 'true';
      // Above the fold content is immediately readable, including slow devices.
      if (element.getBoundingClientRect().top < window.innerHeight) continue;
      element.classList.add('workspace-reveal');
      pending.add(element);
      observer.observe(element);
    }
  };
  new MutationObserver(scan).observe(view, {childList: true});
  reduced.addEventListener('change', () => {
    if (reduced.matches) {
      for (const element of pending) {
        element.classList.add('workspace-revealed');
        observer.unobserve(element);
      }
      pending.clear();
    } else scan();
  });
  scan();
}
