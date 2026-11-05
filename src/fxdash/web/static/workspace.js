// Keep the keyboard shortcut without adding a separate motion system.
const view = document.getElementById('view');
document.querySelector('.skip-link')?.addEventListener('click', event => {
  if (!view) return;
  event.preventDefault();
  view.focus();
  view.scrollIntoView({block: 'start', behavior: 'instant'});
});
