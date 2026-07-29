package main

import (
	"bytes"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestToolDefinitionCount(t *testing.T) {
	if got := len(toolDefinitions()); got != 9 {
		t.Fatalf("toolDefinitions() count = %d, want 9", got)
	}
}

func TestClickMethodSchemaAndParser(t *testing.T) {
	tool := findToolDefinition(t, "click")
	properties := tool.InputSchema["properties"].(map[string]any)
	method := properties["click_method"].(map[string]any)
	values := method["enum"].([]string)
	if strings.Join(values, ",") != "auto,accessibility,app_post,sky_click,global" {
		t.Fatalf("click_method enum = %#v", values)
	}

	for input, want := range map[string]string{
		"":              "auto",
		" AUTO ":        "auto",
		"Accessibility": "accessibility",
		"app_post":      "app_post",
		"SKY_CLICK":     "sky_click",
		"GLOBAL":        "global",
	} {
		got, err := parseClickMethod(input)
		if err != nil {
			t.Fatalf("parseClickMethod(%q): %v", input, err)
		}
		if got != want {
			t.Fatalf("parseClickMethod(%q) = %q, want %q", input, got, want)
		}
	}

	for _, input := range []string{"physical", "targeted"} {
		if _, err := parseClickMethod(input); err == nil || !strings.Contains(err.Error(), "Expected one of: auto, accessibility, app_post, sky_click, global") {
			t.Fatalf("parseClickMethod(%s) error = %v", input, err)
		}
	}
}

func TestLinuxClickMethodSafetyAndPlatformSupport(t *testing.T) {
	x, y := 10.0, 20.0
	service := newService()

	result := service.click("Text Editor", "", &x, &y, 1, "left", "app_post")
	if !result.IsError || result.Content[0].Text != "click_method 'app_post' is not supported on Linux" {
		t.Fatalf("app_post click result = %#v", result)
	}

	result = service.click("Text Editor", "", &x, &y, 1, "left", "sky_click")
	if !result.IsError || result.Content[0].Text != "click_method 'sky_click' is not supported on Linux" {
		t.Fatalf("sky_click result = %#v", result)
	}

	t.Setenv("OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS", "")
	result = service.click("Text Editor", "", &x, &y, 1, "left", "global")
	if !result.IsError || !strings.Contains(result.Content[0].Text, "requires OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS=1") {
		t.Fatalf("unauthorized global click result = %#v", result)
	}

	t.Setenv("OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS", "yes")
	if !globalPointerFallbacksEnabled() {
		t.Fatal("global pointer authorization should accept yes")
	}
}

func TestGetAppStateSchemaIncludesTextLimit(t *testing.T) {
	tool := findToolDefinition(t, "get_app_state")
	properties := tool.InputSchema["properties"].(map[string]any)
	if _, ok := properties["show_full_text"]; ok {
		t.Fatal("get_app_state schema should not expose show_full_text")
	}
	textLimit := properties["text_limit"].(map[string]any)
	anyOf := textLimit["anyOf"].([]any)
	integerLimit := anyOf[0].(map[string]any)
	if got := integerLimit["type"]; got != "integer" {
		t.Fatalf("text_limit integer type = %v, want integer", got)
	}
	if got := integerLimit["minimum"]; got != 1 {
		t.Fatalf("text_limit integer minimum = %v, want 1", got)
	}
	maxLimit := anyOf[1].(map[string]any)
	if got := maxLimit["type"]; got != "string" {
		t.Fatalf("text_limit max type = %v, want string", got)
	}
	enum := maxLimit["enum"].([]string)
	if len(enum) != 1 || enum[0] != "max" {
		t.Fatalf("text_limit enum = %#v, want [max]", enum)
	}
	maxTreeNodes := properties["max_tree_nodes"].(map[string]any)
	if got := maxTreeNodes["type"]; got != "integer" {
		t.Fatalf("max_tree_nodes type = %v, want integer", got)
	}
	if got := maxTreeNodes["minimum"]; got != 1 {
		t.Fatalf("max_tree_nodes minimum = %v, want 1", got)
	}
	maxTreeDepth := properties["max_tree_depth"].(map[string]any)
	if got := maxTreeDepth["type"]; got != "integer" {
		t.Fatalf("max_tree_depth type = %v, want integer", got)
	}
	if got := maxTreeDepth["minimum"]; got != 1 {
		t.Fatalf("max_tree_depth minimum = %v, want 1", got)
	}
	required := tool.InputSchema["required"].([]string)
	if len(required) != 1 || required[0] != "app" {
		t.Fatalf("required = %#v, want [app]", required)
	}
}

func TestParseSnapshotArgsSupportsTextLimit(t *testing.T) {
	app, textLimit, maxTreeNodes, maxTreeDepth, err := parseSnapshotArgs([]string{"--text-limit", "1000", "Text Editor"})
	if err != nil {
		t.Fatal(err)
	}
	if app != "Text Editor" || textLimit == nil || textLimit.runtimeValue() != 1000 || maxTreeNodes != nil || maxTreeDepth != nil {
		t.Fatalf("parseSnapshotArgs = (%q, %#v, %v, %v), want (Text Editor, 1000, nil, nil)", app, textLimit, maxTreeNodes, maxTreeDepth)
	}

	app, textLimit, maxTreeNodes, maxTreeDepth, err = parseSnapshotArgs([]string{"Text Editor", "--text-limit", "max"})
	if err != nil {
		t.Fatal(err)
	}
	if app != "Text Editor" || textLimit == nil || textLimit.runtimeValue() != "max" || maxTreeNodes != nil || maxTreeDepth != nil {
		t.Fatalf("parseSnapshotArgs max = (%q, %#v, %v, %v), want (Text Editor, max, nil, nil)", app, textLimit, maxTreeNodes, maxTreeDepth)
	}

	app, textLimit, maxTreeNodes, maxTreeDepth, err = parseSnapshotArgs([]string{"Text Editor"})
	if err != nil {
		t.Fatal(err)
	}
	if app != "Text Editor" || textLimit != nil || maxTreeNodes != nil || maxTreeDepth != nil {
		t.Fatalf("parseSnapshotArgs default = (%q, %#v, %v, %v), want (Text Editor, nil, nil, nil)", app, textLimit, maxTreeNodes, maxTreeDepth)
	}

	app, textLimit, maxTreeNodes, maxTreeDepth, err = parseSnapshotArgs([]string{"--max-tree-nodes", "3000", "--max-tree-depth", "96", "Text Editor"})
	if err != nil {
		t.Fatal(err)
	}
	if app != "Text Editor" || textLimit != nil || maxTreeNodes == nil || *maxTreeNodes != 3000 || maxTreeDepth == nil || *maxTreeDepth != 96 {
		t.Fatalf("parseSnapshotArgs custom tree budget = (%q, %#v, %v, %v), want (Text Editor, nil, 3000, 96)", app, textLimit, maxTreeNodes, maxTreeDepth)
	}
}

func TestParseSnapshotArgsRejectsInvalidTextLimit(t *testing.T) {
	for _, value := range []string{"0", "-1", "1.5", "full"} {
		if _, _, _, _, err := parseSnapshotArgs([]string{"--text-limit", value, "Text Editor"}); err == nil || err.Error() != "--text-limit must be a positive integer or max" {
			t.Fatalf("invalid text_limit %q error = %v", value, err)
		}
	}
	if _, _, _, _, err := parseSnapshotArgs([]string{"--text-limit"}); err == nil || err.Error() != "--text-limit requires a positive integer or max value" {
		t.Fatalf("missing text_limit error = %v", err)
	}
	if _, _, _, _, err := parseSnapshotArgs([]string{"--show-full-text", "Text Editor"}); err == nil || err.Error() != "unknown snapshot option: --show-full-text" {
		t.Fatalf("old show_full_text flag error = %v", err)
	}
}

func TestParseSnapshotArgsRejectsInvalidTreeBudget(t *testing.T) {
	if _, _, _, _, err := parseSnapshotArgs([]string{"--max-tree-nodes", "0", "Text Editor"}); err == nil || err.Error() != "--max-tree-nodes must be a positive integer" {
		t.Fatalf("invalid max_tree_nodes error = %v", err)
	}
	if _, _, _, _, err := parseSnapshotArgs([]string{"--max-tree-depth", "1.5", "Text Editor"}); err == nil || err.Error() != "--max-tree-depth must be a positive integer" {
		t.Fatalf("invalid max_tree_depth error = %v", err)
	}
	if _, _, _, _, err := parseSnapshotArgs([]string{"--max-tree-nodes"}); err == nil || err.Error() != "--max-tree-nodes requires a positive integer value" {
		t.Fatalf("missing max_tree_nodes error = %v", err)
	}
}

func TestCallSequenceStopsAfterFirstToolError(t *testing.T) {
	output, hasError, err := runCallCommand([]string{
		"--calls",
		`[{"tool":"not_a_tool"},{"tool":"list_apps"}]`,
	}, newService())
	if err != nil {
		t.Fatal(err)
	}
	if !hasError {
		t.Fatal("expected hasError")
	}
	items, ok := output.([]map[string]any)
	if !ok {
		t.Fatalf("output type = %T", output)
	}
	if len(items) != 1 {
		t.Fatalf("sequence output count = %d, want 1", len(items))
	}
}

func TestReadArgumentsAcceptsJSONObject(t *testing.T) {
	args, err := readArguments(`{"app":"Text Editor","pages":2}`, "")
	if err != nil {
		t.Fatal(err)
	}
	if args["app"] != "Text Editor" {
		t.Fatalf("app = %v", args["app"])
	}
	if args["pages"].(json.Number).String() != "2" {
		t.Fatalf("pages = %v", args["pages"])
	}
}

func TestElementIndexAcceptsStringAndJSONNumber(t *testing.T) {
	args, err := readArguments(`{"app":"Text Editor","element_index":0}`, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := optionalElementIndex(args); got != "0" {
		t.Fatalf("numeric element_index = %q, want 0", got)
	}
	if got := optionalElementIndex(map[string]any{"element_index": "14"}); got != "14" {
		t.Fatalf("string element_index = %q, want 14", got)
	}
	if got := optionalElementIndex(map[string]any{"element_index": json.Number("1.5")}); got != "" {
		t.Fatalf("fractional element_index = %q, want empty", got)
	}
}

func TestMCPInitializeResponseContainsToolsCapability(t *testing.T) {
	request := map[string]any{
		"jsonrpc": "2.0",
		"id":      float64(1),
		"method":  "initialize",
		"params":  map[string]any{},
	}
	response := handleMCPRequest(request, newService())
	result, ok := response["result"].(map[string]any)
	if !ok {
		t.Fatalf("missing result: %#v", response)
	}
	capabilities := result["capabilities"].(map[string]any)
	if _, ok := capabilities["tools"]; !ok {
		t.Fatalf("missing tools capability: %#v", capabilities)
	}
}

func TestCLIHelpMentionsLinuxRuntime(t *testing.T) {
	var out bytes.Buffer
	if err := runCLI([]string{"--help"}, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "Open Computer Use for Linux") {
		t.Fatalf("help text did not mention Linux runtime:\n%s", out.String())
	}
}

func TestLinuxRuntimeDocumentsATSPIAndFallbackBoundary(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "Atspi") {
		t.Fatal("Linux runtime must use AT-SPI")
	}
	if !strings.Contains(linuxRuntimeScript, "generate_mouse_event") {
		t.Fatal("Linux runtime should keep coordinate input explicit and visible in the bridge")
	}
	if !strings.Contains(serverInstructions, "not a universal Wayland background input model") {
		t.Fatal("MCP instructions must document the Linux background-input boundary")
	}
}

func TestLinuxRuntimeTextLimitSupportsMaxMode(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "DEFAULT_TEXT_LIMIT = 500") {
		t.Fatal("Linux runtime should define the shared 500 character text limit")
	}
	if !strings.Contains(linuxRuntimeScript, "text_limit=parse_text_limit(operation.get(\"text_limit\"), DEFAULT_TEXT_LIMIT)") {
		t.Fatal("Linux get_app_state should pass text_limit into snapshot rendering")
	}
	if !strings.Contains(linuxRuntimeScript, "if isinstance(value, str) and value.lower() == \"max\"") {
		t.Fatal("Linux runtime should support max text limit mode")
	}
	if !strings.Contains(linuxRuntimeScript, "max_tree_nodes=positive_int(operation.get(\"max_tree_nodes\"), MAX_ELEMENTS)") {
		t.Fatal("Linux get_app_state should pass max_tree_nodes into snapshot rendering")
	}
	if !strings.Contains(linuxRuntimeScript, "max_tree_depth=positive_int(operation.get(\"max_tree_depth\"), MAX_DEPTH)") {
		t.Fatal("Linux get_app_state should pass max_tree_depth into snapshot rendering")
	}
	if !strings.Contains(linuxRuntimeScript, "text_limit + 1") {
		t.Fatal("Linux default truncation should read one extra character so it can append ellipsis")
	}
}

func TestLinuxRuntimeTreeBudgetDefaultsMatchMacOS(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "MAX_ELEMENTS = 1200") {
		t.Fatal("Linux runtime should default to the shared 1200 node tree budget")
	}
	if !strings.Contains(linuxRuntimeScript, "MAX_DEPTH = 64") {
		t.Fatal("Linux runtime should default to the shared 64 level tree depth")
	}
}

func TestLinuxRuntimeEnvironmentDiscoversDesktopSession(t *testing.T) {
	runtimeDir := shortTempDir(t)
	listenUnixSocket(t, filepath.Join(runtimeDir, "bus"))
	listenUnixSocket(t, filepath.Join(runtimeDir, "wayland-0"))

	env := envSliceToMap(linuxRuntimeEnvironmentFrom(
		[]string{"PATH=/usr/bin"},
		os.Getuid(),
		[]map[string]string{{
			"XDG_RUNTIME_DIR":     runtimeDir,
			"DISPLAY":             ":1",
			"XAUTHORITY":          "/tmp/open-computer-use-xauth",
			"XDG_SESSION_TYPE":    "wayland",
			"XDG_CURRENT_DESKTOP": "GNOME",
		}},
	))

	if got := env["XDG_RUNTIME_DIR"]; got != runtimeDir {
		t.Fatalf("XDG_RUNTIME_DIR = %q, want %q", got, runtimeDir)
	}
	if got, want := env["DBUS_SESSION_BUS_ADDRESS"], "unix:path="+filepath.Join(runtimeDir, "bus"); got != want {
		t.Fatalf("DBUS_SESSION_BUS_ADDRESS = %q, want %q", got, want)
	}
	if got := env["WAYLAND_DISPLAY"]; got != "wayland-0" {
		t.Fatalf("WAYLAND_DISPLAY = %q, want wayland-0", got)
	}
	if got := env["DISPLAY"]; got != ":1" {
		t.Fatalf("DISPLAY = %q, want :1", got)
	}
	if got := env["XDG_CURRENT_DESKTOP"]; got != "GNOME" {
		t.Fatalf("XDG_CURRENT_DESKTOP = %q, want GNOME", got)
	}
}

func TestLinuxRuntimeEnvironmentCanonicalizesRuntimeBus(t *testing.T) {
	runtimeDir := shortTempDir(t)
	listenUnixSocket(t, filepath.Join(runtimeDir, "bus"))

	env := envSliceToMap(linuxRuntimeEnvironmentFrom(
		[]string{
			"XDG_RUNTIME_DIR=" + runtimeDir,
			"DBUS_SESSION_BUS_ADDRESS=unix:path=" + filepath.Join(runtimeDir, "bus") + ",guid=stale",
		},
		os.Getuid(),
		nil,
	))

	if got, want := env["DBUS_SESSION_BUS_ADDRESS"], "unix:path="+filepath.Join(runtimeDir, "bus"); got != want {
		t.Fatalf("DBUS_SESSION_BUS_ADDRESS = %q, want %q", got, want)
	}
}

func listenUnixSocket(t *testing.T, path string) {
	t.Helper()
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatalf("listen unix socket %s: %v", path, err)
	}
	t.Cleanup(func() {
		_ = listener.Close()
		_ = os.Remove(path)
	})
}

func TestLinuxRuntimeSelectsEditableTargetByState(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "state_contains(node, Atspi.StateType.EDITABLE)") {
		t.Fatal("Linux runtime must require the EDITABLE state when choosing a text target; interface presence alone matches hidden placeholder widgets")
	}
	if !strings.Contains(linuxRuntimeScript, "candidates.sort(key=lambda item: item[0], reverse=True)") {
		t.Fatal("Linux runtime should rank editable candidates so the focused widget wins")
	}
	if strings.Contains(linuxRuntimeScript, "return find_first(root, is_editable)") {
		t.Fatal("Linux runtime must not fall back to the first editable-text interface in tree order")
	}
}

func TestLinuxRuntimeVerifiesTextInsertionLanded(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "after = safe(lambda: Atspi.Text.get_character_count(text_iface))") {
		t.Fatal("Linux runtime should re-read the character count after insert_text")
	}
	if !strings.Contains(linuxRuntimeScript, "return int(after) > before, before, int(after)") {
		t.Fatal("Atspi.EditableText.insert_text returns True even when nothing is written, so the write must be confirmed by growth")
	}
	if !strings.Contains(linuxRuntimeScript, "return after == payload or after != before") {
		t.Fatal("set_value must confirm the write landed instead of trusting set_text_contents")
	}
}

func TestLinuxRuntimeTypesAtCaretAndReplacesSelection(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "def text_insertion_point(text_iface):") {
		t.Fatal("Linux runtime should resolve an insertion point instead of always appending")
	}
	if !strings.Contains(linuxRuntimeScript, "Atspi.Text.get_caret_offset(text_iface)") {
		t.Fatal("type_text should insert at the caret so it behaves like real typing")
	}
	if !strings.Contains(linuxRuntimeScript, "Atspi.EditableText.delete_text(editable, selection[0], selection[1])") {
		t.Fatal("type_text should replace a non-empty selection, the way typing does")
	}
}

func TestLinuxRuntimeRejectsOffscreenSentinelCoordinates(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "abs(rect.x) > MAX_SANE_EXTENT or abs(rect.y) > MAX_SANE_EXTENT") {
		t.Fatal("unrendered widgets report INT_MIN origins; coordinate actions must not target them")
	}
	if !strings.Contains(linuxRuntimeScript, "MAX_SANE_EXTENT = 100000") {
		t.Fatal("Linux runtime should define the sane extent bound shared by size and origin checks")
	}
}

func TestLinuxRuntimeReportsExecutionPath(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "UNVERIFIED_SYNTHESIS") {
		t.Fatal("Linux runtime should mark synthesis-based actions as unverified")
	}
	for _, note := range []string{
		"Invoked the element's AT-SPI accessibility action.",
		"Wrote the text through the AT-SPI editable-text API and confirmed it ",
		"fell back to ",
	} {
		if !strings.Contains(linuxRuntimeScript, note) {
			t.Fatalf("action notes must distinguish verified from best-effort paths: missing %q", note)
		}
	}
	if !strings.Contains(linuxRuntimeScript, "response[\"notes\"] = notes") {
		t.Fatal("Linux runtime should return the action notes to the Go bridge")
	}
}

func TestActionResultFlagsUnchangedState(t *testing.T) {
	base := func() *appSnapshot {
		return &appSnapshot{
			WindowTitle:    "Doc",
			FocusedSummary: "a text field",
			SelectedText:   "",
			TreeLines:      []string{"0 frame Doc", "1 text hello"},
		}
	}
	if observablyChanged(base(), base()) {
		t.Fatal("identical snapshots must count as unchanged")
	}
	changedTree := base()
	changedTree.TreeLines = []string{"0 frame Doc", "1 text hello world"}
	if !observablyChanged(base(), changedTree) {
		t.Fatal("a different tree must count as changed")
	}
	shorterTree := base()
	shorterTree.TreeLines = []string{"0 frame Doc"}
	if !observablyChanged(base(), shorterTree) {
		t.Fatal("a tree with fewer lines must count as changed")
	}
	changedTitle := base()
	changedTitle.WindowTitle = "Doc *"
	if !observablyChanged(base(), changedTitle) {
		t.Fatal("a different window title must count as changed")
	}
	changedSelection := base()
	changedSelection.SelectedText = "hello"
	if !observablyChanged(base(), changedSelection) {
		t.Fatal("a different selection must count as changed")
	}
	changedFocus := base()
	changedFocus.FocusedSummary = "a button"
	if !observablyChanged(base(), changedFocus) {
		t.Fatal("a different focused element must count as changed")
	}
	// 截图必须排除在外：光标闪烁会让每次动作都"有变化"，信号就废了。
	sameButNewScreenshot := base()
	sameButNewScreenshot.ScreenshotPNGBase64 = "different-pixels"
	if observablyChanged(base(), sameButNewScreenshot) {
		t.Fatal("screenshot noise must not count as an observable change")
	}
}

func TestResultWithNotesPrependsNotesBeforeTree(t *testing.T) {
	snapshot := &appSnapshot{
		App:       appDescriptor{Name: "gedit", PID: 7},
		TreeLines: []string{"0 frame Doc"},
	}
	result := snapshot.resultWithNotes([]string{"first note", "second note"})
	text := result.Content[0].Text
	if !strings.HasPrefix(text, "Note: first note\nNote: second note\n") {
		t.Fatalf("notes should lead the result text, got %q", text)
	}
	if !strings.Contains(text, "0 frame Doc") {
		t.Fatal("the accessibility tree must still be present")
	}
	if snapshot.resultWithNotes(nil).Content[0].Text != snapshot.renderedText() {
		t.Fatal("an action without notes should render exactly as before")
	}
}

func TestLinuxRuntimeGuardsGlobalInputSynthesis(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "def require_window_focus(window, what):") {
		t.Fatal("Linux runtime needs a focus guard before global input synthesis")
	}
	for _, guarded := range []string{
		"require_window_focus(window, \"press_key\")",
		"require_window_focus(window, \"type_text\")",
		"require_window_focus(window, \"scroll\")",
		"require_window_focus(window, \"drag\")",
		"require_window_focus(window, \"click\")",
	} {
		if !strings.Contains(linuxRuntimeScript, guarded) {
			t.Fatalf("global synthesis path is unguarded: missing %s", guarded)
		}
	}
	if !strings.Contains(linuxRuntimeScript, "Refusing to synthesize") {
		t.Fatal("Linux runtime should fail loudly rather than deliver input to whichever window holds focus")
	}
}

func TestLinuxRuntimeFocusesWindowThroughFocusableChild(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "Atspi.StateType.FOCUSABLE") {
		t.Fatal("Linux runtime should look for a FOCUSABLE child; grab_focus on a GTK frame always fails")
	}
	if !strings.Contains(linuxRuntimeScript, "FOCUS_GRAB_CANDIDATES") {
		t.Fatal("Linux runtime should cap focus-grab attempts because grabbing moves real focus inside the app")
	}
	if !strings.Contains(linuxRuntimeScript, "Atspi.StateType.ACTIVE") {
		t.Fatal("Linux runtime should use the window ACTIVE state to decide whether synthesis is safe")
	}
}

func findToolDefinition(t *testing.T, name string) toolDefinition {
	t.Helper()
	for _, tool := range toolDefinitions() {
		if tool.Name == name {
			return tool
		}
	}
	t.Fatalf("missing tool definition %q", name)
	return toolDefinition{}
}

func shortTempDir(t *testing.T) string {
	t.Helper()
	path, err := os.MkdirTemp("/tmp", "ocu-*")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.RemoveAll(path)
	})
	return path
}

func TestMCPInstructionsDocumentGlobalSynthesisFocusContract(t *testing.T) {
	if !strings.Contains(serverInstructions, "Input synthesis on Linux is global") {
		t.Fatal("MCP instructions must tell the agent that synthesis ignores the app argument")
	}
	if !strings.Contains(serverInstructions, "bring the target window to the foreground") {
		t.Fatal("MCP instructions must document that synthesis tools steal focus")
	}
}
