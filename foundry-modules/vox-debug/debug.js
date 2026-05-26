console.log("🚨 VOX DEBUG LOADED");
const div = document.createElement("div");
div.style = "position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 200px; height: 200px; background: blue; color: white; z-index: 100000; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: bold;";
div.innerText = "VOX DEBUG ACTIVE";
document.body.appendChild(div);
