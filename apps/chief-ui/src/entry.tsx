import "./styles.css";
import "./concept2-repro.css";
import "./concept2-refine.css";
import "./concept2-final.css";
import "./voice.css";
import "./main.tsx";

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // The application remains fully usable online if registration is unavailable.
    });
  });
}
