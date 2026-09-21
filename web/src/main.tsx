import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./global.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("ontology-map root element를 찾지 못했습니다.");
}

createRoot(root).render(<App />);
