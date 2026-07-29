const dropZone = document.querySelector("[data-drop-zone]");
if (dropZone) {
  const input = dropZone.querySelector('input[type="file"]');
  const output = dropZone.querySelector("[data-file-name]");
  const showFile = () => {
    output.textContent = input.files.length ? input.files[0].name : "尚未选择文件";
  };
  input.addEventListener("change", showFile);
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove("dragging"));
  }
}

const taskId = document.body.dataset.taskId;
const taskStatus = document.body.dataset.taskStatus;
const terminal = new Set(["published", "review_required", "failed", "skipped", "error"]);
if (taskId && !terminal.has(taskStatus)) {
  const poll = async () => {
    try {
      const response = await fetch(`/api/tasks/${taskId}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) return;
      const task = await response.json();
      if (task.status !== taskStatus || terminal.has(task.status)) {
        window.location.reload();
      }
    } catch (_) {
      // A temporary network error should not replace the current page.
    }
  };
  window.setInterval(poll, 2000);
}
