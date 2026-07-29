const dropZone = document.querySelector("[data-drop-zone]");
if (dropZone) {
  const input = dropZone.querySelector('input[type="file"]');
  const output = dropZone.querySelector("[data-file-name]");
  const submit = document.querySelector("[data-submit-button]");
  const modeInputs = Array.from(document.querySelectorAll('input[name="mode"]'));
  const selectedMode = () => modeInputs.find((option) => option.checked)?.value || "full";
  const updateSubmitLabel = () => {
    submit.textContent = selectedMode() === "summary" ? "生成重点摘要" : "完整转换";
  };
  const showFile = () => {
    const file = input.files[0];
    output.classList.remove("file-error");
    output.textContent = file ? file.name : "尚未选择文件";
  };
  const showError = (message) => {
    input.value = "";
    output.classList.add("file-error");
    output.textContent = message;
  };
  const acceptDroppedFile = (files) => {
    if (files.length !== 1) {
      showError("一次只能上传一份 PDF");
      return;
    }
    const file = files[0];
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showError("只支持 PDF 文件");
      return;
    }
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };

  input.addEventListener("change", showFile);
  for (const modeInput of modeInputs) {
    modeInput.addEventListener("change", updateSubmitLabel);
  }
  updateSubmitLabel();
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropZone.classList.add("dragging");
    });
  }
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragging");
  });
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    dropZone.classList.remove("dragging");
    acceptDroppedFile(Array.from(event.dataTransfer.files));
  });
  dropZone.closest("form").addEventListener("submit", () => {
    submit.disabled = true;
    submit.textContent = selectedMode() === "summary"
      ? "正在生成重点摘要…"
      : "正在完整转换…";
  });
}

const backButton = document.querySelector("[data-back-button]");
if (backButton) {
  backButton.addEventListener("click", (event) => {
    if (window.history.length > 1) {
      event.preventDefault();
      window.history.back();
    }
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
