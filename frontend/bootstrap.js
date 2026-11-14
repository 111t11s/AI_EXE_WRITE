// Keep the loading message visible if the module graph fails to initialize.
(async () => {
  const status = document.getElementById('startup-status');
  try {
    // The HTML import map versions the full graph, including shared auth state.
    const workbench = await import('./workbench.js');
    await workbench.ready;
    status.remove();
  } catch (error) {
    console.error('工作台启动失败：', error);
    document.getElementById('startup-title').textContent = '登录界面加载失败';
    document.getElementById('startup-message').textContent =
      '请按 Ctrl+F5 强制刷新页面。若仍失败，请将下方错误信息提供给维护人员。';
    const detail = document.getElementById('startup-detail');
    detail.textContent = error?.message || String(error);
    detail.hidden = false;
    const retry = document.getElementById('startup-retry');
    retry.hidden = false;
    retry.onclick = () => location.reload();
  }
})();
