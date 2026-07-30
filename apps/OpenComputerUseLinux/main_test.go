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
	// 与官方 schema 的差异都是刻意的，逐条记在这里：
	//   +get_screenshot        只要图不要树的入口（Linux 特有）
	//   +click_xy              坐标点击独立成工具，让通道从名字上可见
	//   perform_secondary_action -> invoke_element_action（#27，用户拍板）
	//   drag -> drag_xy        它没有元素形式，名字该说出来
	if got := len(toolDefinitions()); got != 11 {
		t.Fatalf("toolDefinitions() count = %d, want 11", got)
	}
	// 通道必须能从工具名上看出来，而不是只写在描述里。
	for _, name := range []string{"click_xy", "drag_xy"} {
		findToolDefinition(t, name)
	}
}

func TestConfirmationNotesStateTheirLimit(t *testing.T) {
	// "confirmed it applied" 是过度承诺：回读确认的是控件的值变了，
	// 不是应用采纳了这个值。实测中把行距 combo 写成 "Double" 会回读成功，
	// 但文档的 line-height 纹丝不动——对话框把控件状态与文档状态分开，
	// 只在 OK/Apply 时提交。
	for _, fragment := range []string{
		"CONTROL changed, not that the application adopted the value",
		"require OK/Apply before the value takes effect",
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("confirmation notes must state what they do NOT prove: missing %q", fragment)
		}
	}
	if strings.Contains(linuxRuntimeScript, "confirmed it applied.") {
		t.Fatal("the old over-claiming wording must not come back")
	}
}

func TestDecisionRelevantStatesAreRendered(t *testing.T) {
	// 禁用、选中、展开、勾选这几类状态此前完全不在树里。agent 因此会对着
	// 禁用控件反复点击，也看不出下拉/菜单到底展开没有。
	for _, fragment := range []string{
		// 断言函数存在即可，不钉签名——签名会因为参数增减而变，
		// 那不是这条测试要保护的东西。
		"def state_segment(",
		"NOTABLE_STATES",
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("tree must expose decision-relevant states: missing %q", fragment)
		}
	}
	// 只渲染"非默认"的一侧：每个节点都标 enabled 会淹没信号。
	if strings.Contains(linuxRuntimeScript, `("ENABLED", "enabled")`) {
		t.Fatal("rendering the default side of every state would drown the signal")
	}
	// 绝不从状态缺失反推语义：Nautilus 的文件图标不设 ENABLED/SENSITIVE 却完全
	// 可操作，标成 disabled 会让 agent 跳过真实目标。
	if strings.Contains(linuxRuntimeScript, `marks.append("disabled")`) {
		t.Fatal("inferring 'disabled' from a missing ENABLED state produces false positives on Linux")
	}
	if !strings.Contains(linuxRuntimeScript, "绝不从状态的缺失反推语义") {
		t.Fatal("the rule against inferring from absent states must stay documented in the source")
	}
}

func TestTruncationIsPrioritisedAndVisible(t *testing.T) {
	// 深度优先截断等于按遍历顺序随机丢弃：先到的占满配额，后面的整片消失，
	// 而且 agent 完全不知道树被砍过。
	for _, fragment := range []string{
		"BUDGET_PRESSURE_RATIO",
		"def is_structural_filler(record):",
		"node(s) omitted:",
		"raise max_tree_nodes to see them",
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("truncation must be prioritised and reported: missing %q", fragment)
		}
	}
	if !strings.Contains(linuxRuntimeScript, "not interactable or not on screen") {
		t.Fatal("the omission notice must say what was dropped, not just how many")
	}
}

func TestTreeIsPrunedByDefaultWithEscapeHatch(t *testing.T) {
	// 裁剪判据与 OSWorld 官方 judge_node() 同源。离线评测（13 步 / 9 步元素定向）
	// 显示 22% 压缩率 + 100% 保留率——前提是渲染保真度到位（单元格带 Frame、
	// 角色不硬编码），两者都已修。
	for _, fragment := range []string{
		"def is_interactive_role(role):",
		"PRUNE_ROLE_SUFFIXES",
		"prune=True,",
		`prune=operation.get("prune", True)`,
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("pruning must be on by default: missing %q", fragment)
		}
	}
	// 被裁的节点仍要继续递归子节点——中间容器往往正是有价值控件的父节点。
	if !strings.Contains(linuxRuntimeScript, "中间容器往往正是有价值控件的父节点") {
		t.Fatal("pruning a container must not drop its subtree")
	}
	state := findToolDefinition(t, "get_app_state")
	props, _ := state.InputSchema["properties"].(map[string]any)
	if _, ok := props["prune"]; !ok {
		t.Fatal("get_app_state must expose a prune escape hatch")
	}
}

func TestBrowserRoutingRuleIsExplicit(t *testing.T) {
	// 两个控制平面并存时，agent 会默认"看到 Chrome 就用 AT-SPI 操作它"，
	// 然后在一个 a11y 默认关闭的应用上反复试错。规则必须显式写出来。
	for _, fragment := range []string{
		"Browsers are NOT handled by these tools",
		"separate control plane",
		"handoff point between the two planes is the filesystem",
	} {
		if !strings.Contains(serverInstructions, fragment) {
			t.Fatalf("server instructions must state the browser routing rule: missing %q", fragment)
		}
	}
}

// 观测的两条通道是**互补**，不是主备。
//
// 这条曾经写成"两条独立轨道、截图是 fallback"，`build_snapshot` 也默认不带图，
// 理由是 a11y 轨不该顺带付截图的钱。成本数字至今成立，**但"独立"这个前提被
// 实测推翻了**（手动跑 OSWorld Impress 任务，见 docs/exec-plans/active/
// 20260730-impress-manual-run-findings.md）：
//
//   - 右对齐与保存都真的生效了，a11y 树却字节不变，于是两个动作都被判成
//     "送达但被忽略"——**两次假阴性，都是一张截图一眼判掉的**。
//   - 反过来，"四个没有名字的 spin button 里哪个是 Position Y" 靠截图只能凭
//     标签的空间邻近去猜，靠树的 description 是确定的。
//
// 所以树给**可操作性**，截图给**可验证性**，默认两个都给。
// #29 的 A/B 靠 OPEN_COMPUTER_USE_A11Y_SCREENSHOTS=0 关掉再比。
func TestObservationChannelsAreComplementary(t *testing.T) {
	shot := findToolDefinition(t, "get_screenshot")
	if !strings.Contains(shot.Description, "WITHOUT the accessibility tree") {
		t.Fatal("get_screenshot 现在是「只要图不要树」的入口，不再是 fallback")
	}
	if !strings.Contains(serverInstructions, "COMPLEMENTARY, not interchangeable") {
		t.Fatal("server instructions 必须把两条通道讲成互补而不是主备")
	}
	if !strings.Contains(serverInstructions, "ACTIONABLE") ||
		!strings.Contains(serverInstructions, "VERIFIABLE") {
		t.Fatal("必须分别讲清树的可操作性与截图的可验证性")
	}
	// 假阴性是这次改动的**唯一**理由，必须带着实测证据讲给 agent 听，
	// 否则它会继续把"树没变"当成"动作没生效"。
	if !strings.Contains(serverInstructions, "weak evidence of failure, never as proof") {
		t.Fatal("必须告诉 agent「树没变」只是弱证据")
	}
	if !strings.Contains(linuxRuntimeScript, "def a11y_screenshots_enabled():") {
		t.Fatal("截图策略必须集中在一个可开关的判据里，供 #29 做 A/B")
	}
	if !strings.Contains(linuxRuntimeScript, `SCREENSHOT_REQUIRED_TOOLS = {"drag_xy", "click_xy"}`) {
		t.Fatal("GUI 通道的截图不可关：它们不锚定元素，效果也未必进树")
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
	service := newService()

	result := service.click("Text Editor", "7", "", nil, nil, 1, "left", "app_post")
	if !result.IsError || result.Content[0].Text != "click_method 'app_post' is not supported on Linux" {
		t.Fatalf("app_post click result = %#v", result)
	}

	result = service.click("Text Editor", "7", "", nil, nil, 1, "left", "sky_click")
	if !result.IsError || result.Content[0].Text != "click_method 'sky_click' is not supported on Linux" {
		t.Fatalf("sky_click result = %#v", result)
	}

	// click 不再接受 x/y——坐标点击是另一条通道，另一个工具。
	x, y := 10.0, 20.0
	result = service.click("Text Editor", "7", "", &x, &y, 1, "left", "auto")
	if !result.IsError || !strings.Contains(result.Content[0].Text, "use click_xy") {
		t.Fatalf("click 收到 x/y 应当指向 click_xy: %#v", result)
	}
	result = service.click("Text Editor", "", "", nil, nil, 1, "left", "auto")
	if !result.IsError || !strings.Contains(result.Content[0].Text, "click requires element_index") {
		t.Fatalf("click 缺 element_index 应当明确报错: %#v", result)
	}

	// 原来的 OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS 闸门整个禁掉了
	// 无锚点坐标点击，代价是 GUI 通道默认不可用——而实测多处 AT-SPI 动作
	// 返回成功却不生效（Nautilus 的 menu、GIMP 图层的 activate、VLC 的 Toggle），
	// 此时坐标是唯一出路。它被一条**更强**的保证替换：GUI 通道的坐标在运行时
	// 夹紧在窗口矩形内，风险从"可能打到别的应用"降为"最多打到本窗口边缘"，
	// 且不牺牲能力。
	if strings.Contains(serverInstructions, "OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS") {
		t.Fatal("闸门已移除，instructions 不该再提它")
	}
	for _, fragment := range []string{
		"# 裸坐标**夹紧在窗口矩形内**。",
		"min(max(left + float(x), left), right)",
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("GUI 通道的坐标必须夹紧在窗口内: 缺 %q", fragment)
		}
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
	// 语义调用同样不能当成"生效"的证据：实测 Nautilus / GIMP / VLC 三个应用的
	// AT-SPI 动作都会返回成功却什么都不做。工具必须把这一点说出来，
	// 否则 agent 会据此推进下一步，而真实界面还停在原地。
	if !strings.Contains(linuxRuntimeScript, "UNVERIFIED_SEMANTIC") {
		t.Fatal("Linux runtime should mark semantic AT-SPI actions as unverified too")
	}
	if !strings.Contains(linuxRuntimeScript, "that is not evidence the action took effect") {
		t.Fatal("semantic action notes must state that toolkit acceptance is not proof of effect")
	}
	for _, note := range []string{
		"Invoked the element's AT-SPI accessibility action. ",
		"Wrote the text through the AT-SPI editable-text API and read it back ",
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

func TestIncrementalTreeOnlyWhenSafeAndCheaper(t *testing.T) {
	// 实测依据：无结构变化的步骤上增量省 62%，有结构变化的步骤上反而亏 7%——
	// 增删两边都要付钱。所以判据是行数不变：它同时保证了划算（只有内容变了）
	// 和正确（#15 证明结构一变 element_index 就永久重排，行数不变即无结构变化）。
	base := func(lines ...string) *appSnapshot { return &appSnapshot{TreeLines: lines} }

	if _, ok := incrementalTree(base("a", "b", "c"), base("a", "b", "c")); ok {
		t.Fatal("完全没变时不该走增量——那属于 nothing-changed，另有提示")
	}
	diff, ok := incrementalTree(base("a", "b", "c", "d", "e", "f"),
		base("a", "b", "X", "d", "e", "f"))
	if !ok {
		t.Fatal("只有一行变化时应该走增量")
	}
	if len(diff) != 2 || diff[1] != "X" {
		t.Fatalf("增量应只含表头和变化行，得到 %v", diff)
	}
	if !strings.Contains(diff[0], "keeps the same element_index") {
		t.Fatal("表头必须说明未变的行仍沿用同一个 element_index")
	}
	if _, ok := incrementalTree(base("a", "b"), base("a", "b", "c")); ok {
		t.Fatal("行数变化意味着结构变化，索引会重排，绝不能走增量")
	}
	if _, ok := incrementalTree(base("a", "b", "c"), base("X", "Y", "c")); ok {
		t.Fatal("变化占比过大时增量不划算，应回退全量")
	}
	if _, ok := incrementalTree(nil, base("a")); ok {
		t.Fatal("没有前一份快照时不能走增量")
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
		"require_window_focus(window, \"drag_xy\")",
		"require_window_focus(window, \"click_xy\")",
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

// 语义调用返回成功却什么都没发生时，auto 必须自己改走坐标合成。
//
// 这是实测出来的常态而非边角：Nautilus 文件图标的 menu、GIMP 图层 cell 的
// activate、VLC 单选按钮的 Toggle，三个应用的 AT-SPI 动作都会返回 True
// 却什么都不做。此时 do_action 返回 True，auto 原有的回落
// 分支（只在返回 False 时触发）不会启动，agent 无路可走。
func TestAutoClickRetriesWithSynthesisWhenSemanticDidNothing(t *testing.T) {
	element := &elementRecord{Index: 7, ControlType: "push button", Name: "Yes"}
	semantic := []string{"[semantic] Invoked the element's AT-SPI accessibility action."}

	request := linuxRequest{Tool: "click", ClickMethod: "auto", Element: element}
	if !shouldRetryWithSynthesis(request, semantic) {
		t.Fatal("auto + element + 语义通道，观测无变化时应当重试")
	}

	// accessibility 是调用方显式要求"只走语义"，不能替它合成。
	explicit := request
	explicit.ClickMethod = "accessibility"
	if shouldRetryWithSynthesis(explicit, semantic) {
		t.Fatal("click_method accessibility 表示只走语义，不得自动合成")
	}

	// 已经是合成路径了，重试没有意义。
	synthesized := []string{"[synthesis] Synthesized a coordinate click at (1, 2)."}
	if shouldRetryWithSynthesis(request, synthesized) {
		t.Fatal("已经走过合成的动作不应再重试")
	}

	// 没有元素锚点就没有可信落点，退回原来的"如实报告未确认"。
	anchorless := request
	anchorless.Element = nil
	if shouldRetryWithSynthesis(anchorless, semantic) {
		t.Fatal("没有 element_index 时不得凭空合成坐标点击")
	}

	// 只对 click 生效：其它工具的重复执行可能有副作用。
	other := request
	other.Tool = "invoke_element_action"
	if shouldRetryWithSynthesis(other, semantic) {
		t.Fatal("自动合成重试只对 click 开放")
	}
}

func TestUsedSemanticPathReadsChannelTags(t *testing.T) {
	if !usedSemanticPath([]string{"noise", "[semantic] did a thing"}) {
		t.Fatal("应当识别 [semantic] 标签")
	}
	if usedSemanticPath([]string{"[synthesis] did a thing"}) {
		t.Fatal("[synthesis] 不是语义通道")
	}
	if usedSemanticPath(nil) {
		t.Fatal("没有 Note 时不应判定为语义通道")
	}
}

// 渲染一个节点时，动作表只允许问一次。
//
// LibreOffice 的 ATK 桥被反复问动作表会打出
//
//	(soffice): CRITICAL: impl_get_NActions: assertion 'ATK_IS_ACTION (user_data)' failed
//
// 密集调用下应用会整个退出（实测：对话框循环第 3~4 轮 soffice 消失，
// 随后 get_app_state 报 appNotFound）。早先为了给出 [has-click-action] 标记，
// record_for 经 state_segment -> preferred_action_index 又问了第二次，
// 等于在一个已知脆弱的桥上把每个节点的问询翻倍。
func TestTreeRenderingReadsActionTableOnce(t *testing.T) {
	if !strings.Contains(linuxRuntimeScript, "def node_actions(") {
		t.Fatal("动作表必须有一个一次读完的入口 node_actions()")
	}
	// record_for 里不得再出现第二次动作表读取。
	if strings.Contains(linuxRuntimeScript, "state_segment(node)\n") {
		t.Fatal("state_segment 不应自己再去问一次动作表，标记要由 record_for 传入")
	}
	if !strings.Contains(linuxRuntimeScript, "actions, has_click_action = node_actions(node)") {
		t.Fatal("record_for 应当一次读完动作表，两个字段共用")
	}
}

// 纯合成类工具（键盘/滚动/拖拽）没有第二条通道可回落，所以对它们有价值的
// 不是重试，而是把"送达"和"生效"分开讲——这两件事在这里确实能分开：
// 合成前 require_window_focus 已确认窗口活动，夺不到焦点会硬失败。
// 于是"什么都没变"的含义是**应用收到了但没反应**，同一个按键再按一次不会
// 有不同结果；这与 click 那种"可能压根没送到"是不同的处置。
func TestSynthesisOnlyToolsSeparateDeliveryFromEffect(t *testing.T) {
	for _, tool := range []string{"press_key", "scroll", "drag_xy", "click_xy"} {
		if !deliveryWasVerified(linuxRequest{Tool: tool}) {
			t.Fatalf("%s 走 require_window_focus，送达是可确认的", tool)
		}
	}
	// click 可能走语义通道，压根没经过焦点确认，不能声称送达已验证。
	if deliveryWasVerified(linuxRequest{Tool: "click"}) {
		t.Fatal("click 可能走语义通道，不得声称送达已验证")
	}
	if !strings.Contains(serverInstructions, "element-targeted") {
		t.Fatal("instructions 应当仍然引导优先走元素定向")
	}
	// 两条 Note 会并排出现，措辞必须分清层级：合成通道声明的是"送达**目标控件**
	// 未经验证"，这条声明的是"送达**本窗口**已验证"。不写清层级会读成自相矛盾。
	if !strings.Contains(linuxRuntimeScript, "Delivery to the intended target was not verified") {
		t.Fatal("合成通道仍应声明目标控件级别的不确定性")
	}
}

// a11y 优先的理由是**可操作性**，不是普遍更便宜。
//
// 实测（#29）：gedit 树 349 token vs 窗口截图约 1014 视觉 token（截图贵 2.9x）；
// 文件管理器树 2135 token vs 截图 756（截图反而便宜 0.4x）。
// 早先的措辞暗示截图总是更贵，对内容丰富的应用是错的——工具不该拿一个
// 会被实测推翻的理由去引导 agent。
func TestInstructionsDoNotClaimScreenshotsAreAlwaysCostlier(t *testing.T) {
	if !strings.Contains(serverInstructions, "ACTIONABLE") {
		t.Fatal("应当把可操作性讲成 a11y 优先的理由")
	}
	if strings.Contains(serverInstructions, "it is cheap, precise") {
		t.Fatal("不得再声称 a11y 树总是更便宜——实测在文件管理器上不成立")
	}
	// 措辞随"截图默认恒带"改过一次，但这条教训本身没有被推翻：成本没有固定
	// 高低序，暗示 a11y 总是更省会在文件管理器这类应用上把 agent 引偏。
	if !strings.Contains(serverInstructions, "Do not assume the tree is always the cheaper channel") {
		t.Fatal("应当明确提醒不要假定 a11y 总是更省")
	}
	if !strings.Contains(serverInstructions, "a file manager tree ~2100") {
		t.Fatal("应当给出实测数字，而不是只给一句定性的话")
	}
}

// 工具名本身就是引导：perform_secondary_action 读起来像 fallback，而 a11y
// 语义动作恰恰是首选路径。实测中通道选对与否直接决定任务成败，而名字是模型
// 选通道时最强的信号——比描述强。用户拍板改名（方案 C），接受与官方 schema
// 及 macOS/Windows 的分歧。
func TestSecondaryActionToolIsNamedForItsRealRole(t *testing.T) {
	if strings.Contains(linuxRuntimeScript, "perform_secondary_action") {
		t.Fatal("运行时不应再出现旧工具名")
	}
	tool := findToolDefinition(t, "invoke_element_action")
	if strings.Contains(tool.Description, "additional accessibility action") {
		t.Fatal("旧描述把它讲成 click 之外的附加项，不得回来")
	}
	if !strings.Contains(tool.Description, "not a fallback") {
		t.Fatal("描述应当明说它不是 fallback")
	}
	if !strings.Contains(serverInstructions, "invoke_element_action") {
		t.Fatal("instructions 的工具清单应当同步改名")
	}
}

// 意图声明的交叉核对。抄自 Playwright 的 element 参数（"Human-readable element
// description"），它本意是可读性与权限，副作用正好是**意图可核对**。
//
// 修的是实测踩过的一类**静默**失败：F4 打开对话框后索引全部重排，用上一份快照
// 的下标调 click(element_index=5)，工具照点不误——本想点 Position Y，实际点到
// 菜单，把对象高度误改成 16.26cm，全程没有一条报错。
func TestElementIntentCrossCheck(t *testing.T) {
	spin := &elementRecord{Index: 5, ControlType: "spin button", Description: "Enter the vertical distance"}
	menu := &elementRecord{Index: 5, ControlType: "menu", Name: "Insert"}

	// 就是那次实测：声明"Position Y spin button"，下标却解析到 menu Insert。
	mismatch := elementIntentMismatch(menu, "Position Y spin button")
	if mismatch == "" {
		t.Fatal("下标解析到完全不同的控件时必须拒绝")
	}
	for _, fragment := range []string{`menu "Insert"`, "Position Y spin button", "earlier snapshot"} {
		if !strings.Contains(mismatch, fragment) {
			t.Fatalf("拒绝信息要把两边都说清并给出原因，缺 %q: %s", fragment, mismatch)
		}
	}

	// 回归：第一版把 role 也算进可匹配范围，被最常见的词击穿——声明里几乎必然
	// 有 "button"，role 里也几乎必然有 "button"，于是正要拦的情况照样放行。
	// 实测确认过：声明 "the Save button"、给的却是 New 的下标，当时放行了。
	if elementIntentMismatch(&elementRecord{Index: 8, ControlType: "push button", Name: "New",
		Description: "Create a new document"}, "the Save button") == "" {
		t.Fatal("通用词 button 不得让校验失效")
	}

	// 无名元素的身份常来自旁边的 label，自身属性里没有可比对的东西——
	// 实测 LibreOffice「位置和大小」的四个 spin button 全无名字，
	// "哪个是 Position Y" 只写在旁边的 label 上。这类**判不了就别判**。
	if got := elementIntentMismatch(spin, "Position Y spin button"); got != "" {
		t.Fatalf("无名元素不该校验（否则纯误伤）: %s", got)
	}

	// 同一句声明对上正确的元素时不得拒绝。
	if got := elementIntentMismatch(&elementRecord{ControlType: "push button", Name: "Save",
		Description: "Save the current file"}, "the Save button"); got != "" {
		t.Fatalf("声明与元素相符不该拒绝: %s", got)
	}

	// **误拒比漏过更糟**：被误拒的 agent 会以为目标根本不存在。
	// 以下都必须放行。
	for _, ok := range []struct {
		record   *elementRecord
		declared string
		why      string
	}{
		{menu, "", "没声明就是没开启这个校验，维持旧行为"},
		{menu, "插入菜单", "非 ASCII 声明比不了英文 role/name，判不了就别拒"},
		{menu, "the", "只有虚词，没有可比对的实词"},
		{&elementRecord{ControlType: "push button", Name: "OK"}, "OK", "名字完全命中"},
		{&elementRecord{ControlType: "push button", Name: "Save"}, "save button", "大小写不敏感"},
		{&elementRecord{ControlType: "table cell", Name: "A1"}, "the A1 cell", "虚词之外有实词命中"},
		{&elementRecord{ControlType: "spin button"}, "spin button with no name", "无名元素跳过校验"},
		{&elementRecord{ControlType: "push button", Name: "OK"}, "the button", "只有通用词，没有区分性实词"},
	} {
		if got := elementIntentMismatch(ok.record, ok.declared); got != "" {
			t.Fatalf("误拒（%s）: declared=%q -> %s", ok.why, ok.declared, got)
		}
	}
}

// element 必须出现在所有按 element_index 寻址的工具上，否则这道校验会有缺口，
// 而缺口正好落在 agent 最容易搞错的地方。
func TestIntentDeclarationOfferedOnEveryAccessibilityTool(t *testing.T) {
	for _, name := range []string{"click", "invoke_element_action", "set_value", "scroll"} {
		tool := findToolDefinition(t, name)
		props, _ := tool.InputSchema["properties"].(map[string]any)
		if _, ok := props["element"]; !ok {
			t.Fatalf("%s 缺少 element 意图声明", name)
		}
	}
	// GUI 通道没有元素可声明，不该有这个参数。
	for _, name := range []string{"click_xy", "drag_xy"} {
		tool := findToolDefinition(t, name)
		props, _ := tool.InputSchema["properties"].(map[string]any)
		if _, ok := props["element"]; ok {
			t.Fatalf("%s 是坐标通道，不该有 element 参数", name)
		}
	}
}

// 这两条都是**真实 agent 轨迹**（Claude Code 挂 MCP 跑 OSWorld Impress 任务，
// 官方评估器判 1.0）暴露出来的，不是设想的场景。
func TestNotesLearnedFromTheRealAgentRun(t *testing.T) {
	// ① ctrl+s 之后 "delivered-but-ignored" 说过头了：文件其实存下来了，
	//    agent 却因此多花截图 + 点 Save 两步去自证，占那次 12 步里的 17%。
	//    格式类改动与文件状态**根本不进 a11y 树**，所以"树没变"只能推出
	//    "树看不见"，推不出"没生效"。
	for _, fragment := range []string{
		"WEAK evidence, not proof of failure",
		"Judge this from the attached screenshot",
		"took effect while the tree stayed byte-identical",
	} {
		if !strings.Contains(deliveredButIgnoredNote(), fragment) {
			t.Fatalf("送达但无变化的 Note 不得断言失败，缺 %q", fragment)
		}
	}

	// ② `unknown element_index "128"` 只有 27 个字符、零指引，而同样是"下标
	//    过期"，解析到别的控件时的报错却讲清了原因和出路。两条路径的帮助程度
	//    不该差这么多。
	snapshot := &appSnapshot{Elements: []elementRecord{{Index: 0}, {Index: 59}}}
	_, err := lookupElement(snapshot, "128")
	if err == nil {
		t.Fatal("下标不存在时必须报错")
	}
	for _, fragment := range []string{"0..59", "renumbered", "get_app_state again"} {
		if !strings.Contains(err.Error(), fragment) {
			t.Fatalf("下标不存在的报错要给出范围与出路，缺 %q: %s", fragment, err)
		}
	}
}

// 把那句 Note 单独取出来，测试才不用去猜它藏在哪个分支里。
func deliveredButIgnoredNote() string {
	return strings.Join(unchangedNotes(true), " ")
}
