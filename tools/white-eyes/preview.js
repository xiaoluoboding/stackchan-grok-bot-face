import { createGrokBot } from '../../third_party/grokbot/grok-player.js';
import { createWhiteEyes } from './white-eyes.js';
const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const data = await fetch('../../third_party/grokbot/grok-recipes.json').then(r => r.json());
const state = params.get('state') || 'idle';
const frozenAt = params.has('frozenAt') ? Number(params.get('frozenAt')) : undefined;
if (params.has('face')) document.body.classList.add('face-only');
const opts = { data, size: 320, state, seed: 23, firstPhase: 0, frozenAt };
window.original = createGrokBot($('#original'), { ...opts, theme: { ink:[1,1,1],spot:[0,0,0],cutout:true } });
window.bot = createWhiteEyes($('#new'), opts);
const labels = { idle:'待机',listening:'聆听',thinking:'思考',streaming:'说话',happy:'开心',curious:'好奇',sad:'难过',sleeping:'睡眠',searching:'搜索' };
for (const [value, label] of Object.entries(labels)) {
  const button = document.createElement('button'); button.textContent = label; button.dataset.state = value;
  button.addEventListener('click', () => select(value)); $('#states').append(button);
}
for (const value of Object.keys(data.states)) {
  const option = document.createElement('option'); option.value = value; option.textContent = labels[value] ? `${labels[value]} / ${value}` : value; $('#all').append(option);
}
function select(value) {
  window.bot.setState(value); window.original.setState(value); $('#all').value = value;
  document.querySelectorAll('[data-state]').forEach(b => b.setAttribute('aria-pressed', b.dataset.state === value));
  $('#status').textContent = value.toUpperCase();
}
$('#all').addEventListener('change', e => select(e.target.value));
for (const name of ['scale','thickness']) $(`#${name}`).addEventListener('input', e => {
  window.bot[name === 'scale' ? 'setScale' : 'setThickness'](e.target.value);
  $(`#${name}Value`).value = `${Number(e.target.value).toFixed(2)}×`;
});
$('#blink').onclick = () => { window.bot.blink(); window.original.blink(); };
$('#follow').onclick = e => {
  const enabled = e.currentTarget.getAttribute('aria-pressed') !== 'true';
  e.currentTarget.setAttribute('aria-pressed', enabled); window.bot.setFollow(enabled); window.original.setFollow(enabled);
};
$('#fullscreen').onclick = async () => {
  try { await $('#new').requestFullscreen(); } catch { document.body.classList.add('face-only'); }
};
window.addEventListener('keydown', e => { if (e.key === 'Escape') document.body.classList.remove('face-only'); });
try {
  await Promise.all([window.bot.ready, window.original.ready]);
  if (window.original.error) throw window.original.error;
  select(state); window.previewReady = true;
} catch (error) { $('#status').textContent = `加载失败：${error.message}`; }
