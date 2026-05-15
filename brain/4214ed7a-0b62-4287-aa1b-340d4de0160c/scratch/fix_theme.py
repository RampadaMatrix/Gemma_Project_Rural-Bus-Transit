import os

file_path = r"d:\Gemma_Project_Rural-Bus-Transit\HITL_Pipeline_new\Raptor_data\raptor_visualization_map.html"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update CSS tokens
old_css = """        :root {
            /* Light Theme Tokens */
            --bg-glass: rgba(255, 255, 255, 0.88);
            --bg-blur: 15px;
            --border-glass: rgba(255, 255, 255, 0.3);
            --text-main: #1E272E;
            --text-dim: #485460;
            --accent-main: #FF4757;
            --accent-snap: #2ED573;
            --accent-vill: #747D8C;
            --shadow: 0 12px 40px rgba(0, 0, 0, 0.12);
            --font-head: 'Outfit', sans-serif;
            --font-body: 'Inter', sans-serif;
            --map-invert: 0;
        }

        [data-theme="dark"] {
            --bg-glass: rgba(15, 20, 25, 0.85);
            --border-glass: rgba(255, 255, 255, 0.1);
            --text-main: #F1F2F6;
            --text-dim: #A4B0BE;
            --shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
            --map-invert: 0.95;
        }"""

new_css = """        :root {
            /* Light Theme Tokens - High Contrast */
            --bg-glass: rgba(255, 255, 255, 0.88);
            --bg-blur: 24px;
            --border-glass: rgba(0, 0, 0, 0.1);
            --text-main: #1e293b;
            --text-dim: #475569;
            --accent-main: #FF4757;
            --accent-snap: #10b981;
            --accent-vill: #64748b;
            --shadow: 0 12px 40px rgba(0, 0, 0, 0.08);
            --font-head: 'Outfit', sans-serif;
            --font-body: 'Inter', sans-serif;
            --map-invert: 0;
            --input-bg: rgba(0, 0, 0, 0.05);
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --bg-glass: rgba(15, 23, 42, 0.92);
                --border-glass: rgba(255, 255, 255, 0.12);
                --text-main: #f8fafc;
                --text-dim: #94a3b8;
                --shadow: 0 12px 60px rgba(0, 0, 0, 0.5);
                --map-invert: 0.95;
                --input-bg: rgba(255, 255, 255, 0.1);
            }
        }

        [data-theme="light"] {
            --bg-glass: rgba(255, 255, 255, 0.88);
            --border-glass: rgba(0, 0, 0, 0.1);
            --text-main: #1e293b;
            --text-dim: #475569;
            --map-invert: 0;
            --input-bg: rgba(0, 0, 0, 0.05);
        }

        [data-theme="dark"] {
            --bg-glass: rgba(15, 23, 42, 0.92);
            --border-glass: rgba(255, 255, 255, 0.12);
            --text-main: #f8fafc;
            --text-dim: #94a3b8;
            --shadow: 0 12px 60px rgba(0, 0, 0, 0.5);
            --map-invert: 0.95;
            --input-bg: rgba(255, 255, 255, 0.1);
        }"""

content = content.replace(old_css, new_css)

# 2. Update Theme Logic
old_js = """        // --- Theme Logic ---
        const themeBtn = document.getElementById('theme-btn');
        let currentTheme = 'light';
        themeBtn.addEventListener('click', () => {
            currentTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', currentTheme);
        });"""

new_js = """        // --- Theme Logic ---
        const themeBtn = document.getElementById('theme-btn');
        const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)');
        
        function setTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('raptor-theme', theme);
        }

        // Initialize theme
        const savedTheme = localStorage.getItem('raptor-theme');
        if (savedTheme) {
            setTheme(savedTheme);
        } else if (systemPrefersDark.matches) {
            setTheme('dark');
        }

        themeBtn.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme') || (systemPrefersDark.matches ? 'dark' : 'light');
            setTheme(currentTheme === 'light' ? 'dark' : 'light');
        });

        systemPrefersDark.addEventListener('change', e => {
            if (!localStorage.getItem('raptor-theme')) {
                setTheme(e.matches ? 'dark' : 'light');
            }
        });"""

content = content.replace(old_js, new_js)

# 3. Update search box and other components
content = content.replace("background: rgba(125,125,125,0.06);", "background: var(--input-bg);")
content = content.replace("border: 1px solid rgba(125,125,125,0.15);", "border: 1px solid var(--border-glass);")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Replacement complete.")
