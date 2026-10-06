const products = [
  { name: "Forma Vase", category: "Home", price: 48, color: "CLAY / NATURAL", image: "/assets/vase.svg" },
  { name: "Morning Cup", category: "Home", price: 32, color: "CREAM / MOSS", image: "/assets/cup.svg" },
  { name: "Daylight Lamp", category: "Desk", price: 86, color: "SAND / BRASS", image: "/assets/lamp.svg" },
  { name: "Arc Notebook", category: "Desk", price: 18, color: "PAPER / INK", image: "/assets/notebook.svg" },
  { name: "Sunday Tumbler", category: "Everyday", price: 38, color: "OLIVE / STEEL", image: "/assets/cup.svg" },
  { name: "Still Life Vessel", category: "Everyday", price: 62, color: "TERRA / STONE", image: "/assets/vase.svg" },
];

const grid = document.getElementById("product-grid");
const search = document.getElementById("search");
const empty = document.getElementById("empty-results");
const bagCount = document.getElementById("bag-count");
const bagButton = document.getElementById("bag-button");
const toast = document.getElementById("toast");
let category = "All";
let bag = 0;
let toastTimer;

function render() {
  const query = search.value.trim().toLowerCase();
  const filtered = products.filter((product) =>
    (category === "All" || product.category === category) &&
    `${product.name} ${product.category} ${product.color}`.toLowerCase().includes(query)
  );
  grid.replaceChildren(...filtered.map((product) => {
    const card = document.createElement("article");
    card.className = "product-card";
    card.innerHTML = `<div class="product-image"><img src="${product.image}" alt="" loading="lazy"><span class="product-add" aria-hidden="true">+</span></div><div class="product-info"><div><span class="product-category">${product.category.toUpperCase()} · ${product.color}</span><h3>${product.name}</h3></div><strong>$${product.price}</strong></div>`;
    const button = document.createElement("button");
    button.className = "product-action";
    button.type = "button";
    button.textContent = `Add ${product.name} to bag`;
    button.addEventListener("click", () => {
      bag += 1;
      bagCount.textContent = String(bag);
      bagButton.setAttribute("aria-label", `Shopping bag, ${bag} item${bag === 1 ? "" : "s"}`);
      showToast(`${product.name} added to your bag`);
    });
    card.append(button);
    return card;
  }));
  empty.hidden = filtered.length > 0;
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2600);
}

document.querySelectorAll("[data-category]").forEach((button) => button.addEventListener("click", () => {
  category = button.dataset.category;
  document.querySelectorAll("[data-category]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  render();
}));
search.addEventListener("input", render);
bagButton.addEventListener("click", () => showToast(bag ? `${bag} item${bag === 1 ? "" : "s"} in your bag` : "Your bag is empty"));
render();
