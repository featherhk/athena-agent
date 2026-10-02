# Athena design language

Athena is a place to read, think and get something done. The default interface should feel composed and approachable: warm paper and stone tones, readable serif type, fine lines and a small, distinctive mark. The conversation remains the main surface.

## An interpretive Greek influence

This is a contemporary interpretation, not a historical reconstruction. We take a sense of attention and restraint from the Acropolis Museum's [Relief of the Pensive Athena](https://www.theacropolismuseum.gr/en/relief-pensive-athena). The [Athenian silver tetradrachm at The Met](https://www.metmuseum.org/art/collection/search/330160), with Athena and her owl, informs the choice of emblem. These are design references, not claims about ancient typography or interface design.

The owl is an original line drawing. The wordmark uses restrained, spaced capitals. Avoid temple borders, imitation stone textures, ornamental Greek lettering, mascot expressions and decorative antiquity. Museum images are not embedded in the application.

## Reading and hierarchy

- The default font stack combines Iowan Old Style, Palatino and Georgia with Songti and available CJK serif fonts; Arabic has a Naskh fallback. Reading text is 17px with a generous line height. Fonts are local; no font service is required.
- The persisted `ui_font=system` value now selects Athena's default reading stack. This keeps existing accounts compatible. Explicit font preferences remain available and continue to take precedence.
- Assistant replies sit on the reading surface. User messages have a modest tint and fine border. Typography and spacing establish hierarchy without enclosing every paragraph in another card.
- A single collapsed activity row exposes the run's details when useful. Technical fields use monospaced text; opened details must remain comfortably readable.
- Files are distinct objects with recognizable names and compact previews. An HTML file keeps its authored appearance, including a light paper canvas for unstyled HTML; the surrounding viewer follows the app theme.

## Color and material

The default palette combines warm marble white, ink and low-saturation purple. `--canvas`, `--surface`, `--wash`, `--tint`, `--ink`, `--muted`, `--accent` and `--on-accent` are the shared materials across messages, forms, viewers and inspection panels. A custom color changes these materials together, including readable text and controls. Named palettes are shortcuts.

Use small corner radii, fine borders and shadows only when a floating surface needs separation. Avoid glass effects, ambient glows, canvas gradients and layers of rounded cards. The interface must also remain clear in black and arbitrary custom themes.

## Presence and motion

The default emblem is quiet while idle. Thinking, executing, responding, waiting and stopped states have distinct visual treatments and a readable status label. Reduced motion retains the distinction through static shapes and text.

The emblem starts at 84px and yields space to reading with a 32px compact state. `presence_size` is configurable, subject to available screen space. A versioned `presence` component can replace its form. Neither that replacement nor a full workspace can remove the independent stop and recovery controls.

## Agent-authored surfaces

Extensions inherit the current font and theme tokens. Use an actual layout slot for sidebars and workspaces; reserve overlays for effects that serve the user's request. The host supplies the component title and outer spacing. Begin with the content instead of repeating the title inside another card. Intentional departures are appropriate when the task or user calls for them.

Read `backend/athena/skills/interface/references/bridge.md` for the supported theme and layout interfaces. A registered version must be checked through rendering observations and the requested interaction; attractive HTML alone does not establish task success.

## Review criteria

Review the conversation, input, waiting-for-user form, file preview and developer panel together. Check narrow layouts, long text, Arabic direction, black and arbitrary themes, reduced motion and the expanded/compact emblem transition. Preserve the user's content and current work during visual iteration. New controls should earn their place by helping complete a task.
