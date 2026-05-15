import argparse
import datetime
import hashlib
import os
import re
import shutil
from pathlib import Path


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def backup_file(src: Path) -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = src.with_suffix(src.suffix + f".bak_{ts}")
    shutil.copy2(src, backup)
    return backup


def replace_once(content: str, old: str, new: str, label: str) -> str:
    if old not in content:
        raise RuntimeError(f"Could not find target text for replacement: {label}")
    if content.count(old) != 1:
        raise RuntimeError(
            f"Expected exactly 1 occurrence for replacement '{label}', found {content.count(old)}"
        )
    return content.replace(old, new)


def replace_once_idempotent(content: str, old: str, new: str, label: str) -> str:
    if old in content:
        return replace_once(content, old, new, label)
    # If the old text isn't present but the new text is, treat it as already patched.
    if content.count(new) == 1:
        return content
    raise RuntimeError(f"Could not find old or new text for replacement: {label}")


def replace_once_regex(content: str, pattern: str, repl, label: str) -> str:
    rx = re.compile(pattern, flags=re.MULTILINE)
    matches = list(rx.finditer(content))
    if not matches:
        raise RuntimeError(f"Could not find target text for regex replacement: {label}")
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly 1 regex match for '{label}', found {len(matches)}")
    return rx.sub(repl, content, count=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        default=str(Path("HITL_Pipeline_new") / "route_verification_map.html"),
        help="Path to route_verification_map.html",
    )
    parser.add_argument(
        "--rollback",
        default="",
        help="Path to a .bak_* backup file to restore over the target file.",
    )
    args = parser.parse_args()

    target = Path(args.file)
    if not target.exists():
        raise SystemExit(f"Target file not found: {target}")

    if args.rollback:
        backup = Path(args.rollback)
        if not backup.exists():
            raise SystemExit(f"Rollback backup not found: {backup}")
        shutil.copy2(backup, target)
        print(f"Rolled back {target} from {backup}")
        return 0

    original = target.read_text(encoding="utf-8")
    original_hash = sha256_text(original)

    backup = backup_file(target)
    print(f"Backup created: {backup}")

    updated = original

    # 1) Smoothen oscillation transition timing
    # Allow re-running the script after a previous patch (idempotent).
    updated = replace_once_regex(
        updated,
        r"transition:\n\s+grid-template-columns (?:420|520)ms var\(--ease-cinematic\),\n\s+width (?:420|520)ms var\(--ease-cinematic\),\n\s+transform (?:260|320)ms var\(--ease-snappy\),\n\s+box-shadow (?:260|320)ms var\(--ease-snappy\);",
        "transition:\n                grid-template-columns 520ms var(--ease-cinematic),\n                width 520ms var(--ease-cinematic),\n                transform 320ms var(--ease-snappy),\n                box-shadow 320ms var(--ease-snappy);",
        "gp-turn transition",
    )

    # 2) Focus proportions (Thinking | Reply | User)
    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="user"] {\n            grid-template-columns: minmax(118px, 14fr) minmax(170px, 22fr) minmax(240px, 64fr);\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="user"] {\n            grid-template-columns:\n                minmax(96px, 10fr)\n                minmax(120px, 20fr)\n                minmax(220px, 70fr);\n        }',
        "focus=user proportions",
    )

    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="thinking"] {\n            grid-template-columns: minmax(300px, 58fr) minmax(150px, 18fr) minmax(190px, 24fr);\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="thinking"] {\n            grid-template-columns:\n                minmax(220px, 70fr)\n                minmax(96px, 10fr)\n                minmax(120px, 20fr);\n        }',
        "focus=thinking proportions",
    )

    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="reply"] {\n            grid-template-columns: minmax(120px, 14fr) minmax(460px, 68fr) minmax(150px, 18fr);\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="reply"] {\n            grid-template-columns:\n                minmax(72px, 5fr)\n                minmax(320px, 80fr)\n                minmax(96px, 15fr);\n        }',
        "focus=reply proportions",
    )

    # 2b) Later "ANDROID NOTIFICATION COMPACTING MODEL" overrides (these can hide the difference)
    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="thinking"] {\n            grid-template-columns:\n                minmax(220px, 34fr)\n                minmax(0, 48fr)\n                minmax(104px, 18fr) !important;\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="thinking"] {\n            grid-template-columns:\n                minmax(220px, 70fr)\n                minmax(96px, 10fr)\n                minmax(120px, 20fr) !important;\n        }',
        "compacting focus=thinking proportions",
    )

    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="reply"] {\n            grid-template-columns:\n                minmax(84px, 10fr)\n                minmax(0, 72fr)\n                minmax(112px, 18fr) !important;\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="reply"] {\n            grid-template-columns:\n                minmax(72px, 5fr)\n                minmax(0, 80fr)\n                minmax(96px, 15fr) !important;\n        }',
        "compacting focus=reply proportions",
    )

    updated = replace_once_idempotent(
        updated,
        '#gemma-panel .gp-turn[data-gp-focus="user"] {\n            grid-template-columns:\n                minmax(84px, 10fr)\n                minmax(0, 58fr)\n                minmax(220px, 32fr) !important;\n        }',
        '#gemma-panel .gp-turn[data-gp-focus="user"] {\n            grid-template-columns:\n                minmax(96px, 10fr)\n                minmax(0, 20fr)\n                minmax(220px, 70fr) !important;\n        }',
        "compacting focus=user proportions",
    )

    # Align transition timing in compacting section so oscillation remains noticeable
    updated = replace_once_idempotent(
        updated,
        'transition:\n                grid-template-columns 360ms var(--ease-cinematic),\n                max-height 260ms var(--ease-snappy),\n                box-shadow 180ms ease,\n                border-color 180ms ease !important;',
        'transition:\n                grid-template-columns 520ms var(--ease-cinematic),\n                max-height 260ms var(--ease-snappy),\n                box-shadow 220ms ease,\n                border-color 220ms ease !important;',
        "compacting gp-turn transition",
    )

    # 3) prefers-reduced-motion safeguard (insert right after gp-turn-cell block)
    marker = (
        "#gemma-panel .gp-turn-cell {\n"
        "            align-items: stretch;\n"
        "            justify-content: flex-start;\n"
        "            min-height: 58px;\n"
        "            padding: 13px 16px;\n"
        "            min-width: 0;\n"
        "            cursor: pointer;\n"
        "            transition:\n"
        "                opacity 240ms ease,\n"
        "                background 240ms ease,\n"
        "                padding 420ms var(--ease-cinematic);\n"
        "        }"
    )
    if marker not in updated:
        raise RuntimeError("Could not find gp-turn-cell block marker for inserting reduced-motion CSS")

    reduced_motion_css = (
        "\n\n        @media (prefers-reduced-motion: reduce) {\n"
        "            #gemma-panel .gp-turn {\n"
        "                transition: none !important;\n"
        "            }\n"
        "            #gemma-panel .gp-turn-cell {\n"
        "                transition: none !important;\n"
        "            }\n"
        "        }"
    )
    if reduced_motion_css not in updated:
        updated = updated.replace(marker, marker + reduced_motion_css)

    # 3b) Make focus oscillation visually obvious (beyond column widths)
    emphasis_id = "transform: translateY(-1px) scale(1.015);"
    if emphasis_id not in updated:
        focus_highlight_block = (
            "        #gemma-panel .gp-turn[data-gp-focus=\"thinking\"] .gp-cell-thinking,\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"reply\"] .gp-cell-reply,\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"user\"] .gp-cell-user {\n"
            "            opacity: 1;\n"
            "            background: linear-gradient(180deg, rgba(255,255,255,0.085), rgba(255,255,255,0.025));\n"
            "        }\n"
        )
        focus_highlight_block = "".join(focus_highlight_block)

        focus_emphasis_css = (
            "\n        #gemma-panel .gp-turn[data-gp-focus=\"thinking\"] .gp-cell-thinking,\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"reply\"] .gp-cell-reply,\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"user\"] .gp-cell-user {\n"
            "            transform: translateY(-1px) scale(1.015);\n"
            "            box-shadow: 0 18px 46px rgba(2, 6, 23, 0.22);\n"
            "        }\n\n"
            "        #gemma-panel .gp-turn[data-gp-focus] .gp-turn-cell {\n"
            "            transform: translateZ(0);\n"
            "        }\n\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"thinking\"] .gp-turn-cell:not(.gp-cell-thinking),\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"reply\"] .gp-turn-cell:not(.gp-cell-reply),\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"user\"] .gp-turn-cell:not(.gp-cell-user) {\n"
            "            opacity: 0.22;\n"
            "        }\n"
        )
        focus_emphasis_css = "".join(focus_emphasis_css)

        updated = replace_once_idempotent(
            updated,
            focus_highlight_block,
            focus_highlight_block + focus_emphasis_css,
            "focus highlight rule + emphasis",
        )

    # 4) Do not force input focus while processing (lets thinking/reply take focus)
    updated = replace_once_idempotent(
        updated,
        "            if (input) input.focus();",
        "            if (input && !isProcessing) input.focus();",
        "setGemmaProcessingUI focus behavior",
    )

    # 5) Smooth route-card expand/collapse (avoid distracting layout snap)
    updated = replace_once_idempotent(
        updated,
        "        #gemma-panel .gp-r-body {\n"
        "            position: relative;\n"
        "            z-index: 2;\n"
        "            display: block;\n"
        "            max-height: 0;\n"
        "            opacity: 0;\n"
        "            overflow: hidden;\n"
        "            transform: translateY(-6px);\n"
        "            padding: 0 20px;\n"
        "            background: transparent;\n"
        "            pointer-events: none;\n"
        "            will-change: max-height, opacity, transform;\n"
        "            transition:\n"
        "                max-height 520ms var(--ease-cinematic),\n"
        "                opacity 220ms ease,\n"
        "                transform 520ms var(--ease-cinematic),\n"
        "                padding 520ms var(--ease-cinematic);\n"
        "        }\n\n"
        "        #gemma-panel .gp-r-card.expanded .gp-r-body {\n"
        "            max-height: min(58vh, 720px);\n"
        "            opacity: 1;\n"
        "            transform: translateY(0);\n"
        "            padding: 0 20px 20px;\n"
        "            pointer-events: auto;\n"
        "        }",
        "        #gemma-panel .gp-r-body {\n"
        "            position: relative;\n"
        "            z-index: 2;\n"
        "            display: block;\n"
        "            max-height: 0;\n"
        "            opacity: 0;\n"
        "            overflow: hidden;\n"
        "            transform: translateY(-6px);\n"
        "            padding: 0 20px;\n"
        "            background: transparent;\n"
        "            pointer-events: none;\n"
        "            will-change: opacity, transform;\n"
        "            transition:\n"
        "                opacity 180ms ease,\n"
        "                transform 260ms var(--ease-spring);\n"
        "        }\n\n"
        "        #gemma-panel .gp-r-card.expanded .gp-r-body {\n"
        "            max-height: min(58vh, 720px);\n"
        "            opacity: 1;\n"
        "            transform: translateY(0);\n"
        "            padding: 0 20px 20px;\n"
        "            pointer-events: auto;\n"
        "        }",
        "smooth route-card expand",
    )

    # 6) Smooth sibling shifting using FLIP (animate transforms, not layout)
    insert_after = (
        "        function setGemmaTurnFocus(element, focus) {\n"
        "            const turn = element?.closest ? element.closest('.gp-turn') : null;\n"
        "            if (!turn) return;\n"
        "            turn.dataset.gpFocus = focus || 'user';\n"
        "        }\n\n"
    )

    old_flip_fn = (
        "        function gemmaFlipAnimateCards(container) {\n"
        "            if (!container) return;\n"
        "            const items = Array.from(container.querySelectorAll('.gp-r-card'));\n"
        "            if (items.length < 2) return;\n"
        "            const first = new Map();\n"
        "            items.forEach(el => first.set(el, el.getBoundingClientRect()));\n"
        "            requestAnimationFrame(() => {\n"
        "                items.forEach(el => {\n"
        "                    const a = first.get(el);\n"
        "                    const b = el.getBoundingClientRect();\n"
        "                    const dx = a.left - b.left;\n"
        "                    const dy = a.top - b.top;\n"
        "                    if (!dx && !dy) return;\n"
        "                    try {\n"
        "                        el.getAnimations?.().forEach(anim => anim.cancel());\n"
        "                    } catch (_) {}\n"
        "                    el.animate(\n"
        "                        [\n"
        "                            { transform: `translate(${dx}px, ${dy}px)` },\n"
        "                            { transform: 'translate(0, 0)' }\n"
        "                        ],\n"
        "                        { duration: 260, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'both' }\n"
        "                    );\n"
        "                });\n"
        "            });\n"
        "        }\n\n"
    )

    new_flip_fn = (
        "        function gemmaFlipAnimateCards(container, mutator) {\n"
        "            if (!container) { mutator?.(); return; }\n"
        "            const items = Array.from(container.querySelectorAll('.gp-r-card'));\n"
        "            if (items.length < 2) { mutator?.(); return; }\n"
        "            const first = new Map();\n"
        "            items.forEach(el => first.set(el, el.getBoundingClientRect()));\n"
        "            mutator?.();\n"
        "            requestAnimationFrame(() => {\n"
        "                items.forEach(el => {\n"
        "                    const a = first.get(el);\n"
        "                    const b = el.getBoundingClientRect();\n"
        "                    const dx = a.left - b.left;\n"
        "                    const dy = a.top - b.top;\n"
        "                    if (!dx && !dy) return;\n"
        "                    try {\n"
        "                        el.getAnimations?.().forEach(anim => anim.cancel());\n"
        "                    } catch (_) {}\n"
        "                    el.animate(\n"
        "                        [\n"
        "                            { transform: `translate(${dx}px, ${dy}px)` },\n"
        "                            { transform: 'translate(0, 0)' }\n"
        "                        ],\n"
        "                        { duration: 320, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'both' }\n"
        "                    );\n"
        "                });\n"
        "            });\n"
        "        }\n\n"
    )

    if "function gemmaFlipAnimateCards" not in updated:
        updated = replace_once_idempotent(
            updated,
            insert_after,
            insert_after + new_flip_fn,
            "insert gemmaFlipAnimateCards",
        )
    else:
        updated = replace_once_idempotent(
            updated,
            old_flip_fn,
            new_flip_fn,
            "upgrade gemmaFlipAnimateCards",
        )

    old_toggle = (
        "        function toggleGemmaRouteCard(card) {\n"
        "            if (!card) return;\n"
        "            const container = card.closest('.gp-raptor-cards');\n"
        "            if (container) gemmaFlipAnimateCards(container);\n"
        "            const turn = card.closest('.gp-turn');\n"
        "            if (turn) {\n"
        "                document.querySelectorAll('#gpBody .gp-turn.gp-turn-expanded').forEach(other => {\n"
        "                    if (other !== turn) other.classList.remove('gp-turn-expanded');\n"
        "                });\n"
        "                turn.dataset.gpFocus = 'reply';\n"
        "                turn.classList.add('gp-turn-expanded');\n"
        "            }\n"
        "            const willExpand = !card.classList.contains('expanded');\n"
        "            container?.querySelectorAll('.gp-r-card.expanded').forEach(other => {\n"
        "                if (other !== card) other.classList.remove('expanded');\n"
        "            });\n"
        "            card.classList.toggle('expanded', willExpand);\n"
        "            if (container) gemmaFlipAnimateCards(container);\n"
        "        }"
    )

    new_toggle = (
        "        function toggleGemmaRouteCard(card) {\n"
        "            if (!card) return;\n"
        "            const container = card.closest('.gp-raptor-cards');\n"
        "            gemmaFlipAnimateCards(container, () => {\n"
        "                const turn = card.closest('.gp-turn');\n"
        "                if (turn) {\n"
        "                    document.querySelectorAll('#gpBody .gp-turn.gp-turn-expanded').forEach(other => {\n"
        "                        if (other !== turn) other.classList.remove('gp-turn-expanded');\n"
        "                    });\n"
        "                    turn.dataset.gpFocus = 'reply';\n"
        "                    turn.classList.add('gp-turn-expanded');\n"
        "                }\n"
        "                const willExpand = !card.classList.contains('expanded');\n"
        "                container?.querySelectorAll('.gp-r-card.expanded').forEach(other => {\n"
        "                    if (other !== card) other.classList.remove('expanded');\n"
        "                });\n"
        "                card.classList.toggle('expanded', willExpand);\n"
        "            });\n"
        "        }"
    )

    updated = replace_once_idempotent(
        updated,
        old_toggle,
        new_toggle,
        "patch toggleGemmaRouteCard for FLIP",
    )

    # 7) Dark mode polish: separate panel from background + boost text legibility
    dark_polish_id = "/* GP DARK POLISH */"
    if dark_polish_id not in updated:
        panel_block_tail = (
            "            transition:\n"
            "                opacity var(--dur-macro) var(--ease-snappy),\n"
            "                filter var(--dur-macro) var(--ease-snappy),\n"
            "                transform var(--dur-macro) var(--ease-cinematic),\n"
            "                box-shadow var(--dur-macro) var(--ease-snappy),\n"
            "                width var(--dur-macro) var(--ease-cinematic),\n"
            "                height var(--dur-macro) var(--ease-cinematic);\n"
            "        }\n"
        )

        dark_polish_css = (
            "\n        /* GP DARK POLISH */\n"
            "        #gemma-panel {\n"
            "            box-shadow:\n"
            "                0 36px 92px rgba(0, 0, 0, 0.72),\n"
            "                0 0 0 1px rgba(255, 255, 255, 0.06) inset,\n"
            "                0 0 54px rgba(99, 102, 241, 0.16);\n"
            "            border-color: rgba(129, 140, 248, 0.22);\n"
            "            background:\n"
            "                radial-gradient(circle at 16% 12%, rgba(99, 102, 241, 0.18), transparent 30%),\n"
            "                radial-gradient(circle at 87% 27%, rgba(249, 115, 22, 0.14), transparent 27%),\n"
            "                radial-gradient(circle at 46% 94%, rgba(56, 189, 248, 0.12), transparent 38%),\n"
            "                linear-gradient(180deg, rgba(12, 18, 38, 0.94), rgba(6, 10, 26, 0.92));\n"
            "        }\n\n"
            "        #gemma-panel .gp-turn-cell .gp-msg-content,\n"
            "        #gemma-panel .gp-turn-cell .gp-thought-content,\n"
            "        #gemma-panel .gp-text-input {\n"
            "            color: rgba(248, 250, 252, 0.98);\n"
            "            text-shadow: 0 0 18px rgba(148, 163, 184, 0.16), 0 1px 0 rgba(0, 0, 0, 0.35);\n"
            "        }\n\n"
            "        #gemma-panel .gp-cell-label {\n"
            "            color: rgba(199, 210, 254, 0.92);\n"
            "            text-shadow: 0 0 14px rgba(129, 140, 248, 0.22);\n"
            "        }\n"
        )
        dark_polish_css = "".join(dark_polish_css)

        updated = replace_once_idempotent(
            updated,
            panel_block_tail,
            panel_block_tail + dark_polish_css,
            "inject dark polish css",
        )

    # 8) Orange/gold masked gradient ring around the panel (strong separation)
    ring_id = "/* GP ORANGE RING */"
    if ring_id not in updated:
        anchor = "        /* GP DARK POLISH */\n"
        ring_css = (
            "\n        /* GP ORANGE RING */\n"
            "        #gemma-panel::before {\n"
            "            background: linear-gradient(135deg,\n"
            "                rgba(251, 146, 60, 0.92),\n"
            "                rgba(245, 158, 11, 0.82),\n"
            "                rgba(255, 215, 128, 0.22),\n"
            "                rgba(129, 140, 248, 0.18)\n"
            "            ) !important;\n"
            "            opacity: 0.78;\n"
            "            filter: drop-shadow(0 0 16px rgba(251, 146, 60, 0.22)) drop-shadow(0 0 34px rgba(245, 158, 11, 0.12));\n"
            "        }\n\n"
            "        body.theme-light #gemma-panel::before,\n"
            "        body.light #gemma-panel::before {\n"
            "            opacity: 0.55;\n"
            "            filter: none;\n"
            "        }\n"
        )
        ring_css = "".join(ring_css)
        updated = replace_once_idempotent(
            updated,
            anchor,
            anchor + ring_css,
            "inject orange ring css",
        )

    # 8b) Remove mask-based ring: switch to non-masked gradient border (background-clip)
    ring_nomask_id = "/* GP ORANGE RING (NO MASK) */"
    if ring_nomask_id not in updated:
        old_ring_block = (
            "        /* GP ORANGE RING */\n"
            "        #gemma-panel::before {\n"
            "            background: linear-gradient(135deg,\n"
            "                rgba(251, 146, 60, 0.92),\n"
            "                rgba(245, 158, 11, 0.82),\n"
            "                rgba(255, 215, 128, 0.22),\n"
            "                rgba(129, 140, 248, 0.18)\n"
            "            ) !important;\n"
            "            opacity: 0.78;\n"
            "            filter: drop-shadow(0 0 16px rgba(251, 146, 60, 0.22)) drop-shadow(0 0 34px rgba(245, 158, 11, 0.12));\n"
            "        }\n\n"
            "        body.theme-light #gemma-panel::before,\n"
            "        body.light #gemma-panel::before {\n"
            "            opacity: 0.55;\n"
            "            filter: none;\n"
            "        }\n"
        )

        new_ring_block = (
            "        /* GP ORANGE RING (NO MASK) */\n"
            "        #gemma-panel::before {\n"
            "            opacity: 0 !important;\n"
            "            filter: none !important;\n"
            "        }\n\n"
            "        #gemma-panel {\n"
            "            border: 2px solid transparent;\n"
            "            background-origin: padding-box, border-box;\n"
            "            background-clip: padding-box, border-box;\n"
            "            background-image:\n"
            "                radial-gradient(circle at 16% 12%, rgba(99, 102, 241, 0.18), transparent 30%),\n"
            "                radial-gradient(circle at 87% 27%, rgba(249, 115, 22, 0.14), transparent 27%),\n"
            "                radial-gradient(circle at 46% 94%, rgba(56, 189, 248, 0.12), transparent 38%),\n"
            "                linear-gradient(180deg, rgba(12, 18, 38, 0.94), rgba(6, 10, 26, 0.92)),\n"
            "                linear-gradient(135deg, rgba(251, 146, 60, 0.92), rgba(245, 158, 11, 0.78), rgba(255, 215, 128, 0.18), rgba(129, 140, 248, 0.16));\n"
            "        }\n"
        )

        updated = replace_once_idempotent(
            updated,
            old_ring_block,
            new_ring_block,
            "convert orange ring to no-mask",
        )

    # 9) Remove the top lane strip (THINKING | GEMMA REPLY | USER)
    lane_strip_id = "<!-- GP COMMAND STRIP REMOVED -->"
    if lane_strip_id not in updated:
        old_lane_strip = (
            "                <div class=\"gp-command-strip\" aria-label=\"Gemma operations context\">\n"
            "                    <div class=\"gp-op-chip\">\n"
            "                        <div class=\"gp-op-k\">Lane</div>\n"
            "                        <div class=\"gp-op-v\">Thinking</div>\n"
            "                    </div>\n"
            "                    <div class=\"gp-op-chip\">\n"
            "                        <div class=\"gp-op-k\">Lane</div>\n"
            "                        <div class=\"gp-op-v\">Gemma Reply</div>\n"
            "                    </div>\n"
            "                    <div class=\"gp-op-chip\">\n"
            "                        <div class=\"gp-op-k\">Lane</div>\n"
            "                        <div class=\"gp-op-v\">User</div>\n"
            "                    </div>\n"
            "                </div>\n"
        )
        updated = replace_once_idempotent(
            updated,
            old_lane_strip,
            "                <!-- GP COMMAND STRIP REMOVED -->\n",
            "remove gp-command-strip",
        )

    # 10) Boost thinking + user lane text visibility a bit
    lane_boost_id = "/* GP LANE TEXT BOOST */"
    if lane_boost_id not in updated:
        anchor = "        /* GP DARK POLISH */\n"
        boost_css = (
            "\n        /* GP LANE TEXT BOOST */\n"
            "        #gemma-panel .gp-cell-thinking .gp-thought-content {\n"
            "            color: rgba(226, 232, 240, 0.94) !important;\n"
            "            text-shadow: 0 0 16px rgba(196, 181, 253, 0.16), 0 1px 0 rgba(0, 0, 0, 0.35);\n"
            "        }\n\n"
            "        #gemma-panel .gp-cell-user .gp-msg-content {\n"
            "            color: rgba(226, 232, 240, 0.96) !important;\n"
            "            text-shadow: 0 0 16px rgba(96, 165, 250, 0.14), 0 1px 0 rgba(0, 0, 0, 0.35);\n"
            "        }\n\n"
            "        /* When reply is focused, don't over-dim thinking/user lanes */\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"reply\"] .gp-turn-cell.gp-cell-thinking,\n"
            "        #gemma-panel .gp-turn[data-gp-focus=\"reply\"] .gp-turn-cell.gp-cell-user {\n"
            "            opacity: 0.34 !important;\n"
            "        }\n"
        )
        boost_css = "".join(boost_css)
        updated = replace_once_idempotent(
            updated,
            anchor,
            anchor + boost_css,
            "inject lane text boost",
        )

    # 11) Input: remove scrolling + hide scrollbar glyph, use ellipsis truncation
    input_noscroll_id = "/* GP INPUT NO SCROLL */"
    if input_noscroll_id not in updated:
        anchor = "        /* GP DARK POLISH */\n"
        noscroll_css = (
            "\n        /* GP INPUT NO SCROLL */\n"
            "        #gemma-panel .gp-input-box {\n"
            "            overflow: hidden;\n"
            "        }\n\n"
            "        #gemma-panel .gp-text-input {\n"
            "            overflow: hidden;\n"
            "            scrollbar-width: none;\n"
            "            -ms-overflow-style: none;\n"
            "            white-space: nowrap;\n"
            "            text-overflow: ellipsis;\n"
            "        }\n\n"
            "        #gemma-panel .gp-text-input::-webkit-scrollbar {\n"
            "            width: 0;\n"
            "            height: 0;\n"
            "            display: none;\n"
            "        }\n\n"
            "        #gemma-panel .gp-text-input::placeholder {\n"
            "            white-space: nowrap;\n"
            "            overflow: hidden;\n"
            "            text-overflow: ellipsis;\n"
            "        }\n"
        )
        noscroll_css = "".join(noscroll_css)
        updated = replace_once_idempotent(
            updated,
            anchor,
            anchor + noscroll_css,
            "inject input no-scroll css",
        )

    # 12) Fix drag/resize: use setProperty with !important to override CSS, fix top-left handle logic
    drag_resize_fix_id = "/* GP DRAG RESIZE FIXED */"
    if drag_resize_fix_id in updated:
        # Already has the old marker, need to replace with bottom-right logic
        old_drag_resize_current = (
            "            /* GP DRAG RESIZE FIXED */\n"
            "            // ── DRAG (use setProperty with !important to override CSS) ──\n"
            "            let dragState = { active: false, offsetX: 0, offsetY: 0, startRight: 0, startBottom: 0 };\n"
            "            let dragRaf = null;\n"
            "            let dragLastX = 0;\n"
            "            let dragLastY = 0;\n\n"
            "            header.addEventListener('mousedown', (e) => {\n"
            "                if (e.target.closest('.gp-hdr-btn')) return;\n"
            "                dragState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                dragState.offsetX = e.clientX - rect.left;\n"
            "                dragState.offsetY = e.clientY - rect.top;\n"
            "                dragState.startRight = window.innerWidth - rect.right;\n"
            "                dragState.startBottom = window.innerHeight - rect.bottom;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!dragState.active) return;\n"
            "                dragLastX = e.clientX - dragState.offsetX;\n"
            "                dragLastY = e.clientY - dragState.offsetY;\n"
            "                if (dragRaf) return;\n"
            "                dragRaf = requestAnimationFrame(() => {\n"
            "                    dragRaf = null;\n"
            "                    const x = dragLastX;\n"
            "                    const y = dragLastY;\n"
            "                    const maxX = window.innerWidth - 100;\n"
            "                    const maxY = window.innerHeight - 60;\n"
            "                    panel.style.setProperty('left', Math.max(0, Math.min(x, maxX)) + 'px', 'important');\n"
            "                    panel.style.setProperty('top', Math.max(0, Math.min(y, maxY)) + 'px', 'important');\n"
            "                    panel.style.setProperty('right', 'auto', 'important');\n"
            "                    panel.style.setProperty('bottom', 'auto', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (dragState.active) {\n"
            "                    dragState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (dragRaf) {\n"
            "                        cancelAnimationFrame(dragRaf);\n"
            "                        dragRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n\n"
            "            // ── RESIZE (from top-left corner, FIXED logic) ──\n"
            "            let resizeState = { active: false, startX: 0, startY: 0, startW: 0, startH: 0, startLeft: 0, startTop: 0 };\n"
            "            let resizeRaf = null;\n"
            "            let resizeLastClientX = 0;\n"
            "            let resizeLastClientY = 0;\n\n"
            "            resizeHandle.addEventListener('mousedown', (e) => {\n"
            "                resizeState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                resizeState.startX = e.clientX;\n"
            "                resizeState.startY = e.clientY;\n"
            "                resizeState.startW = rect.width;\n"
            "                resizeState.startH = rect.height;\n"
            "                resizeState.startLeft = rect.left;\n"
            "                resizeState.startTop = rect.top;\n"
            "                panel.style.setProperty('right', 'auto', 'important');\n"
            "                panel.style.setProperty('bottom', 'auto', 'important');\n"
            "                panel.style.setProperty('left', rect.left + 'px', 'important');\n"
            "                panel.style.setProperty('top', rect.top + 'px', 'important');\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "                e.stopPropagation();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!resizeState.active) return;\n"
            "                resizeLastClientX = e.clientX;\n"
            "                resizeLastClientY = e.clientY;\n"
            "                if (resizeRaf) return;\n"
            "                resizeRaf = requestAnimationFrame(() => {\n"
            "                    resizeRaf = null;\n"
            "                    const dx = resizeLastClientX - resizeState.startX;\n"
            "                    const dy = resizeLastClientY - resizeState.startY;\n"
            "                    const minW = 390;\n"
            "                    const minH = 430;\n"
            "                    const maxW = Math.min(2200, window.innerWidth - 32);\n"
            "                    const maxH = window.innerHeight - 32;\n"
            "                    const newW = Math.max(minW, Math.min(maxW, resizeState.startW + dx));\n"
            "                    const newH = Math.max(minH, Math.min(maxH, resizeState.startH + dy));\n"
            "                    panel.style.setProperty('width', newW + 'px', 'important');\n"
            "                    panel.style.setProperty('height', newH + 'px', 'important');\n"
            "                    // FIXED: For top-left handle, left/top move WITH the cursor\n"
            "                    panel.style.setProperty('left', (resizeState.startLeft + dx) + 'px', 'important');\n"
            "                    panel.style.setProperty('top', (resizeState.startTop + dy) + 'px', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (resizeState.active) {\n"
            "                    resizeState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (resizeRaf) {\n"
            "                        cancelAnimationFrame(resizeRaf);\n"
            "                        resizeRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n"
        )
        
        new_drag_resize_bottom_right = (
            "            /* GP DRAG RESIZE FIXED */\n"
            "            // ── DRAG (use setProperty with !important to override CSS) ──\n"
            "            let dragState = { active: false, offsetX: 0, offsetY: 0, startRight: 0, startBottom: 0 };\n"
            "            let dragRaf = null;\n"
            "            let dragLastX = 0;\n"
            "            let dragLastY = 0;\n\n"
            "            header.addEventListener('mousedown', (e) => {\n"
            "                if (e.target.closest('.gp-hdr-btn')) return;\n"
            "                dragState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                dragState.offsetX = e.clientX - rect.left;\n"
            "                dragState.offsetY = e.clientY - rect.top;\n"
            "                dragState.startRight = window.innerWidth - rect.right;\n"
            "                dragState.startBottom = window.innerHeight - rect.bottom;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!dragState.active) return;\n"
            "                dragLastX = e.clientX - dragState.offsetX;\n"
            "                dragLastY = e.clientY - dragState.offsetY;\n"
            "                if (dragRaf) return;\n"
            "                dragRaf = requestAnimationFrame(() => {\n"
            "                    dragRaf = null;\n"
            "                    const x = dragLastX;\n"
            "                    const y = dragLastY;\n"
            "                    const maxX = window.innerWidth - 100;\n"
            "                    const maxY = window.innerHeight - 60;\n"
            "                    panel.style.setProperty('left', Math.max(0, Math.min(x, maxX)) + 'px', 'important');\n"
            "                    panel.style.setProperty('top', Math.max(0, Math.min(y, maxY)) + 'px', 'important');\n"
            "                    panel.style.setProperty('right', 'auto', 'important');\n"
            "                    panel.style.setProperty('bottom', 'auto', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (dragState.active) {\n"
            "                    dragState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (dragRaf) {\n"
            "                        cancelAnimationFrame(dragRaf);\n"
            "                        dragRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n\n"
            "            // ── RESIZE (from bottom-right corner, simplified logic) ──\n"
            "            let resizeState = { active: false, startX: 0, startY: 0, startW: 0, startH: 0 };\n"
            "            let resizeRaf = null;\n"
            "            let resizeLastClientX = 0;\n"
            "            let resizeLastClientY = 0;\n\n"
            "            resizeHandle.addEventListener('mousedown', (e) => {\n"
            "                resizeState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                resizeState.startX = e.clientX;\n"
            "                resizeState.startY = e.clientY;\n"
            "                resizeState.startW = rect.width;\n"
            "                resizeState.startH = rect.height;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "                e.stopPropagation();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!resizeState.active) return;\n"
            "                resizeLastClientX = e.clientX;\n"
            "                resizeLastClientY = e.clientY;\n"
            "                if (resizeRaf) return;\n"
            "                resizeRaf = requestAnimationFrame(() => {\n"
            "                    resizeRaf = null;\n"
            "                    const dx = resizeLastClientX - resizeState.startX;\n"
            "                    const dy = resizeLastClientY - resizeState.startY;\n"
            "                    const minW = 390;\n"
            "                    const minH = 430;\n"
            "                    const maxW = Math.min(2200, window.innerWidth - 32);\n"
            "                    const maxH = window.innerHeight - 32;\n"
            "                    const newW = Math.max(minW, Math.min(maxW, resizeState.startW + dx));\n"
            "                    const newH = Math.max(minH, Math.min(maxH, resizeState.startH + dy));\n"
            "                    panel.style.setProperty('width', newW + 'px', 'important');\n"
            "                    panel.style.setProperty('height', newH + 'px', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (resizeState.active) {\n"
            "                    resizeState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (resizeRaf) {\n"
            "                        cancelAnimationFrame(resizeRaf);\n"
            "                        resizeRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n"
        )
        
        updated = replace_once_idempotent(
            updated,
            old_drag_resize_current,
            new_drag_resize_bottom_right,
            "update resize to bottom-right corner",
        )
    else:
        old_drag_resize = (
            "            // ── DRAG ──\n"
            "            let dragState = { active: false, offsetX: 0, offsetY: 0 };\n"
            "            let dragRaf = null;\n"
            "            let dragLastX = 0;\n"
            "            let dragLastY = 0;\n\n"
            "            header.addEventListener('mousedown', (e) => {\n"
            "                if (e.target.closest('.gp-hdr-btn')) return; // Don't drag if clicking buttons\n"
            "                dragState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                dragState.offsetX = e.clientX - rect.left;\n"
            "                dragState.offsetY = e.clientY - rect.top;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!dragState.active) return;\n"
            "                dragLastX = e.clientX - dragState.offsetX;\n"
            "                dragLastY = e.clientY - dragState.offsetY;\n"
            "                if (dragRaf) return;\n"
            "                dragRaf = requestAnimationFrame(() => {\n"
            "                    dragRaf = null;\n"
            "                    const x = dragLastX;\n"
            "                    const y = dragLastY;\n"
            "                    // Switch to top/left positioning for free movement\n"
            "                    panel.style.right = 'auto';\n"
            "                    panel.style.bottom = 'auto';\n"
            "                    panel.style.left = Math.max(0, Math.min(x, window.innerWidth - 100)) + 'px';\n"
            "                    panel.style.top = Math.max(0, Math.min(y, window.innerHeight - 60)) + 'px';\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (dragState.active) {\n"
            "                    dragState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (dragRaf) {\n"
            "                        cancelAnimationFrame(dragRaf);\n"
            "                        dragRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n\n"
            "            // ── RESIZE (from top-left corner, FIXED logic) ──\n"
            "            let resizeState = { active: false, startX: 0, startY: 0, startW: 0, startH: 0, startLeft: 0, startTop: 0 };\n"
            "            let resizeRaf = null;\n"
            "            let resizeLastClientX = 0;\n"
            "            let resizeLastClientY = 0;\n\n"
            "            resizeHandle.addEventListener('mousedown', (e) => {\n"
            "                resizeState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                resizeState.startX = e.clientX;\n"
            "                resizeState.startY = e.clientY;\n"
            "                resizeState.startW = rect.width;\n"
            "                resizeState.startH = rect.height;\n"
            "                resizeState.startLeft = rect.left;\n"
            "                resizeState.startTop = rect.top;\n"
            "                panel.style.setProperty('right', 'auto', 'important');\n"
            "                panel.style.setProperty('bottom', 'auto', 'important');\n"
            "                panel.style.setProperty('left', rect.left + 'px', 'important');\n"
            "                panel.style.setProperty('top', rect.top + 'px', 'important');\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "                e.stopPropagation();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!resizeState.active) return;\n"
            "                resizeLastClientX = e.clientX;\n"
            "                resizeLastClientY = e.clientY;\n"
            "                if (resizeRaf) return;\n"
            "                resizeRaf = requestAnimationFrame(() => {\n"
            "                    resizeRaf = null;\n"
            "                    const dx = resizeLastClientX - resizeState.startX;\n"
            "                    const dy = resizeLastClientY - resizeState.startY;\n"
            "                    const minW = 390;\n"
            "                    const minH = 430;\n"
            "                    const maxW = Math.min(2200, window.innerWidth - 32);\n"
            "                    const maxH = window.innerHeight - 32;\n"
            "                    const newW = Math.max(minW, Math.min(maxW, resizeState.startW + dx));\n"
            "                    const newH = Math.max(minH, Math.min(maxH, resizeState.startH + dy));\n"
            "                    panel.style.setProperty('width', newW + 'px', 'important');\n"
            "                    panel.style.setProperty('height', newH + 'px', 'important');\n"
            "                    // FIXED: For top-left handle, left/top move WITH the cursor\n"
            "                    panel.style.setProperty('left', (resizeState.startLeft + dx) + 'px', 'important');\n"
            "                    panel.style.setProperty('top', (resizeState.startTop + dy) + 'px', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (resizeState.active) {\n"
            "                    resizeState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (resizeRaf) {\n"
            "                        cancelAnimationFrame(resizeRaf);\n"
            "                        resizeRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n"
        )

        new_drag_resize = (
            "            /* GP DRAG RESIZE FIXED */\n"
            "            // ── DRAG (use setProperty with !important to override CSS) ──\n"
            "            let dragState = { active: false, offsetX: 0, offsetY: 0, startRight: 0, startBottom: 0 };\n"
            "            let dragRaf = null;\n"
            "            let dragLastX = 0;\n"
            "            let dragLastY = 0;\n\n"
            "            header.addEventListener('mousedown', (e) => {\n"
            "                if (e.target.closest('.gp-hdr-btn')) return;\n"
            "                dragState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                dragState.offsetX = e.clientX - rect.left;\n"
            "                dragState.offsetY = e.clientY - rect.top;\n"
            "                dragState.startRight = window.innerWidth - rect.right;\n"
            "                dragState.startBottom = window.innerHeight - rect.bottom;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!dragState.active) return;\n"
            "                dragLastX = e.clientX - dragState.offsetX;\n"
            "                dragLastY = e.clientY - dragState.offsetY;\n"
            "                if (dragRaf) return;\n"
            "                dragRaf = requestAnimationFrame(() => {\n"
            "                    dragRaf = null;\n"
            "                    const x = dragLastX;\n"
            "                    const y = dragLastY;\n"
            "                    const maxX = window.innerWidth - 100;\n"
            "                    const maxY = window.innerHeight - 60;\n"
            "                    panel.style.setProperty('left', Math.max(0, Math.min(x, maxX)) + 'px', 'important');\n"
            "                    panel.style.setProperty('top', Math.max(0, Math.min(y, maxY)) + 'px', 'important');\n"
            "                    panel.style.setProperty('right', 'auto', 'important');\n"
            "                    panel.style.setProperty('bottom', 'auto', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (dragState.active) {\n"
            "                    dragState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (dragRaf) {\n"
            "                        cancelAnimationFrame(dragRaf);\n"
            "                        dragRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n\n"
            "            // ── RESIZE (from bottom-right corner, simplified logic) ──\n"
            "            let resizeState = { active: false, startX: 0, startY: 0, startW: 0, startH: 0 };\n"
            "            let resizeRaf = null;\n"
            "            let resizeLastClientX = 0;\n"
            "            let resizeLastClientY = 0;\n\n"
            "            resizeHandle.addEventListener('mousedown', (e) => {\n"
            "                resizeState.active = true;\n"
            "                const rect = panel.getBoundingClientRect();\n"
            "                resizeState.startX = e.clientX;\n"
            "                resizeState.startY = e.clientY;\n"
            "                resizeState.startW = rect.width;\n"
            "                resizeState.startH = rect.height;\n"
            "                panel.style.transition = 'none';\n"
            "                e.preventDefault();\n"
            "                e.stopPropagation();\n"
            "            });\n\n"
            "            window.addEventListener('mousemove', (e) => {\n"
            "                if (!resizeState.active) return;\n"
            "                resizeLastClientX = e.clientX;\n"
            "                resizeLastClientY = e.clientY;\n"
            "                if (resizeRaf) return;\n"
            "                resizeRaf = requestAnimationFrame(() => {\n"
            "                    resizeRaf = null;\n"
            "                    const dx = resizeLastClientX - resizeState.startX;\n"
            "                    const dy = resizeLastClientY - resizeState.startY;\n"
            "                    const minW = 390;\n"
            "                    const minH = 430;\n"
            "                    const maxW = Math.min(2200, window.innerWidth - 32);\n"
            "                    const maxH = window.innerHeight - 32;\n"
            "                    const newW = Math.max(minW, Math.min(maxW, resizeState.startW + dx));\n"
            "                    const newH = Math.max(minH, Math.min(maxH, resizeState.startH + dy));\n"
            "                    panel.style.setProperty('width', newW + 'px', 'important');\n"
            "                    panel.style.setProperty('height', newH + 'px', 'important');\n"
            "                });\n"
            "            });\n\n"
            "            window.addEventListener('mouseup', () => {\n"
            "                if (resizeState.active) {\n"
            "                    resizeState.active = false;\n"
            "                    panel.style.transition = '';\n"
            "                    if (resizeRaf) {\n"
            "                        cancelAnimationFrame(resizeRaf);\n"
            "                        resizeRaf = null;\n"
            "                    }\n"
            "                }\n"
            "            });\n"
        )

        updated = replace_once_idempotent(
            updated,
            old_drag_resize,
            new_drag_resize,
            "fix drag/resize with setProperty and correct top-left logic",
        )

    # 13) Remove !important from CSS position/dimension properties to allow JS drag/resize
    css_important_id = "/* GP CSS IMPORTANT REMOVED */"
    if css_important_id not in updated:
        # Remove !important from position/dimension properties in FLUID OPERATIONS section
        replacements = [
            # FLUID OPERATIONS section (line ~6987-6992)
            ("            left: clamp(48px, 8vw, 160px) !important;\n", "            left: clamp(48px, 8vw, 160px);\n"),
            ("            right: clamp(48px, 8vw, 160px) !important;\n", "            right: clamp(48px, 8vw, 160px);\n"),
            ("            top: calc(var(--toolbar-h) + clamp(18px, 2.3vh, 34px)) !important;\n", "            top: calc(var(--toolbar-h) + clamp(18px, 2.3vh, 34px));\n"),
            ("            bottom: calc(var(--status-h) + clamp(18px, 3vh, 44px)) !important;\n", "            bottom: calc(var(--status-h) + clamp(18px, 3vh, 44px));\n"),
            ("            width: auto !important;\n", "            width: auto;\n"),
            ("            height: auto !important;\n", "            height: auto;\n"),
            ("            min-width: 0 !important;\n", "            min-width: 0;\n"),
            # fleet/surgery hidden (line ~7001-7002)
            ("            left: clamp(48px, 8vw, 160px) !important;\n", "            left: clamp(48px, 8vw, 160px);\n"),
            ("            right: clamp(48px, 8vw, 160px) !important;\n", "            right: clamp(48px, 8vw, 160px);\n"),
            # fullscreen (line ~7006-7008)
            ("            inset: 8px !important;\n", "            inset: 8px;\n"),
            ("            width: calc(100% - 16px) !important;\n", "            width: calc(100% - 16px);\n"),
            ("            height: calc(100% - 16px) !important;\n", "            height: calc(100% - 16px);\n"),
            # min-width 1800px (line ~7244-7246)
            ("            left: clamp(96px, 8vw, 180px) !important;\n", "            left: clamp(96px, 8vw, 180px);\n"),
            ("            right: clamp(96px, 8vw, 180px) !important;\n", "            right: clamp(96px, 8vw, 180px);\n"),
            # max-width 1280px (line ~7256-7258)
            ("            left: clamp(28px, 4vw, 56px) !important;\n", "            left: clamp(28px, 4vw, 56px);\n"),
            ("            right: clamp(28px, 4vw, 56px) !important;\n", "            right: clamp(28px, 4vw, 56px);\n"),
            # max-width 980px (line ~7276-7280)
            ("            left: 12px !important;\n", "            left: 12px;\n"),
            ("            right: 12px !important;\n", "            right: 12px;\n"),
            ("            top: calc(var(--toolbar-h) + 12px) !important;\n", "            top: calc(var(--toolbar-h) + 12px);\n"),
            ("            bottom: calc(var(--status-h) + 12px) !important;\n", "            bottom: calc(var(--status-h) + 12px);\n"),
            # route card footprint (line ~7486-7495)
            ("            left: clamp(132px, 15vw, 330px) !important;\n", "            left: clamp(132px, 15vw, 330px);\n"),
            ("            right: clamp(132px, 15vw, 330px) !important;\n", "            right: clamp(132px, 15vw, 330px);\n"),
            ("            left: clamp(132px, 15vw, 330px) !important;\n", "            left: clamp(132px, 15vw, 330px);\n"),
            ("            right: clamp(132px, 15vw, 330px) !important;\n", "            right: clamp(132px, 15vw, 330px);\n"),
            # max-width 1280px in route card (line ~7695-7697)
            ("            left: clamp(42px, 7vw, 92px) !important;\n", "            left: clamp(42px, 7vw, 92px);\n"),
            ("            right: clamp(42px, 7vw, 92px) !important;\n", "            right: clamp(42px, 7vw, 92px);\n"),
            # max-width 980px in route card (line ~7702-7704)
            ("            left: 12px !important;\n", "            left: 12px;\n"),
            ("            right: 12px !important;\n", "            right: 12px;\n"),
            # max-width 760px (line ~5857-5860)
            ("            left: 10px !important;\n", "            left: 10px;\n"),
            ("            right: 10px !important;\n", "            right: 10px;\n"),
            ("            bottom: calc(var(--status-h) + 10px) !important;\n", "            bottom: calc(var(--status-h) + 10px);\n"),
            ("            width: auto !important;\n", "            width: auto;\n"),
            # max-width 980px in FINAL LANDING (line ~6284-6288)
            ("            left: 14px !important;\n", "            left: 14px;\n"),
            ("            right: 14px !important;\n", "            right: 14px;\n"),
            # fullscreen in FINAL LANDING (line ~6348-6350)
            ("            inset: 8px !important;\n", "            inset: 8px;\n"),
            ("            width: calc(100% - 16px) !important;\n", "            width: calc(100% - 16px);\n"),
            ("            height: calc(100% - 16px) !important;\n", "            height: calc(100% - 16px);\n"),
        ]
        
        for old, new in replacements:
            updated = updated.replace(old, new)

    # 14) Move resize handle to bottom-right and simplify JavaScript logic
    resize_handle_fix_id = "/* GP RESIZE HANDLE FIXED */"
    if resize_handle_fix_id not in updated:
        # Fix the resize handle CSS to be at bottom-right
        old_resize_css = (
            "        #gemma-panel .gp-resize-handle {\n"
            "            position: absolute;\n"
            "            top: 0;\n"
            "            left: 0;\n"
            "            z-index: 20;\n"
            "            width: 24px;\n"
            "            height: 24px;\n"
            "            cursor: nwse-resize;\n"
            "        }\n"
        )
        new_resize_css = (
            "        #gemma-panel .gp-resize-handle {\n"
            "            position: absolute;\n"
            "            bottom: 0;\n"
            "            right: 0;\n"
            "            z-index: 20;\n"
            "            width: 24px;\n"
            "            height: 24px;\n"
            "            cursor: nwse-resize;\n"
            "        }\n"
        )
        updated = replace_once_idempotent(
            updated,
            old_resize_css,
            new_resize_css,
            "move resize handle to bottom-right",
        )

    if updated == original:
        raise RuntimeError("No changes were made (updated content identical to original).")

    target.write_text(updated, encoding="utf-8")
    updated_hash = sha256_text(updated)

    print(f"Patched: {target}")
    print(f"Original SHA256: {original_hash}")
    print(f"Updated  SHA256: {updated_hash}")
    print("If anything looks wrong, rollback with:")
    print(f"  python {Path(__file__).name} --file \"{target}\" --rollback \"{backup}\"")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
