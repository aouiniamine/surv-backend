const ranges = {
  7: { projects: "12", projectsChange: "+2 this week", tasks: "84", tasksChange: "+18%", velocity: "92%", velocityChange: "+6%" },
  30: { projects: "18", projectsChange: "+5 this month", tasks: "326", tasksChange: "+24%", velocity: "89%", velocityChange: "+4%" },
  90: { projects: "25", projectsChange: "+11 this quarter", tasks: "1,024", tasksChange: "+31%", velocity: "94%", velocityChange: "+9%" },
};

document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    const data = ranges[button.dataset.range];
    document.getElementById("projects-value").textContent = data.projects;
    document.getElementById("tasks-value").textContent = data.tasks;
    document.getElementById("velocity-value").textContent = data.velocity;
    document.getElementById("projects-change").textContent = data.projectsChange;
    document.getElementById("tasks-change").textContent = data.tasksChange;
    document.getElementById("velocity-change").textContent = data.velocityChange;
    document.querySelectorAll("[data-range]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  });
});
