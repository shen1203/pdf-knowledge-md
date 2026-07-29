const dropZone = document.querySelector("[data-drop-zone]");
if (dropZone) {
  const input = dropZone.querySelector('input[type="file"]');
  const output = dropZone.querySelector("[data-file-name]");
  const submit = document.querySelector("[data-submit-button]");
  input.addEventListener("change", () => {
    output.textContent = input.files.length ? input.files[0].name : "尚未选择文件";
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove("dragging"));
  }
  dropZone.closest("form").addEventListener("submit", () => {
    submit.disabled = true;
    submit.textContent = "正在上传并转换…";
  });
}

const taskId = document.body.dataset.taskId;
const taskStatus = document.body.dataset.taskStatus;
const terminal = new Set(["completed", "incomplete", "error"]);
if (taskId && !terminal.has(taskStatus)) {
  const poll = async () => {
    try {
      const response = await fetch(`/api/result/${taskId}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) return;
      const task = await response.json();
      if (task.status !== taskStatus || terminal.has(task.status)) {
        window.location.reload();
      }
    } catch (_) {
      // Keep waiting when a temporary network error occurs.
    }
  };
  window.setInterval(poll, 1500);
}
