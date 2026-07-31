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
	//   +find                  不 dump 整棵树的定位查询（Playwright locator 的对位）
	//   +verify                带重试的断言（Playwright expect 的对位）
	if got := len(toolDefinitions()); got != 13 {
		t.Fatalf("toolDefinitions() count = %d, want 13", got)
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
	// 照 Playwright 的错误形态：工具名前缀 + 结构化字段 + Call log + 下一步。
	// 替换掉的是 `unknown element_index "128"` 那种 27 字符零指引。
	for _, fragment := range []string{
		"indices 0..59",
		"Call log:",
		"resolving element_index=128",
		"Next:",
		"renumbered",
		"get_app_state again",
		"survives renumbering",
	} {
		if !strings.Contains(err.Error(), fragment) {
			t.Fatalf("下标不存在的报错要给出完整事实链与出路，缺 %q:\n%s", fragment, err)
		}
	}
	// 快照年龄是我们特有的必需信息：桌面上快照可能陈旧几十秒而 agent 毫无察觉。
	if !strings.Contains(err.Error(), "Snapshot:") {
		t.Fatalf("报错要说清快照有多旧:\n%s", err)
	}
}

// 把那句 Note 单独取出来，测试才不用去猜它藏在哪个分支里。
func deliveredButIgnoredNote() string {
	return strings.Join(unchangedNotes(true, pixelsUnknown, false), " ")
}

// P4：选择器要能替代数字下标，语法与快照行**逐字一致**。
//
// 数字下标每次 get_app_state 都会重排，是 agent 最容易犯错的地方——意图声明
// 只是把那个错误变得**响亮**，没有消除它。选择器从快照行抄下来就能用。
func TestSelectorLookup(t *testing.T) {
	snapshot := &appSnapshot{Elements: []elementRecord{
		{Index: 4, ControlType: "push button", Name: "Save"},
		{Index: 8, ControlType: "push button", Name: "New"},
		{Index: 9, ControlType: "menu", Name: "Save"},
		{Index: 12, ControlType: "label", Name: ""},
	}}

	for _, ok := range []struct {
		selector string
		want     int
	}{
		{`push button "Save"`, 4},
		{`menu "Save"`, 9},
		{`push button "New"`, 8},
	} {
		record, err := lookupElement(snapshot, ok.selector)
		if err != nil {
			t.Fatalf("%s 应当命中: %v", ok.selector, err)
		}
		if record.Index != ok.want {
			t.Fatalf("%s 命中了 %d，期望 %d", ok.selector, record.Index, ok.want)
		}
	}

	// 歧义时**不许挑一个**——静默挑一个正是本项目一直在修的那类错误。
	_, err := lookupElement(snapshot, `"Save"`)
	if err == nil {
		t.Fatal(`"Save" 同时命中 push button 与 menu，必须报歧义`)
	}
	// 候选行的形态照 Playwright 的 strict mode 违规报错：下标 + 描述 + `aka` 一个
	// 可直接使用的替代选择器。
	for _, fragment := range []string{"ambiguous", "matches 2 elements",
		`4    push button "Save"`, `9    menu "Save"`, "aka", "adding the role"} {
		if !strings.Contains(err.Error(), fragment) {
			t.Fatalf("歧义报错要列出候选与收敛办法，缺 %q: %s", fragment, err)
		}
	}

	// 收敛建议要看**缺的是哪一半**：只给了 role 的选择器，该让它补名字，
	// 而不是补一个它已经给了的 role。
	_, err = lookupElement(snapshot, "push button")
	if err == nil {
		t.Fatal("只给 role 且命中多个时必须报歧义")
	}
	if !strings.Contains(err.Error(), `adding the name in quotes`) ||
		!strings.Contains(err.Error(), "push button \"Save\"") {
		t.Fatalf("只给 role 时应当建议补名字: %s", err)
	}

	// 找不到时要说清选择器怎么写。
	_, err = lookupElement(snapshot, `push button "Nope"`)
	if err == nil || !strings.Contains(err.Error(), "no element matches") {
		t.Fatalf("找不到时要明确报出来: %v", err)
	}

	// 数字下标仍然照旧。
	if record, err := lookupElement(snapshot, "8"); err != nil || record.Index != 8 {
		t.Fatalf("数字下标不得失效: %v", err)
	}
}

// P5：通道整体可关，且被关掉的工具**根本不出现在 tools/list 里**。
//
// 这个区别很要紧：模型看得见的工具会去试，试了被拒就是浪费一轮；看不见就不会试。
// Playwright 把坐标做成 opt-in 能力（--caps=vision）也是这个道理。
func TestChannelCapabilitySwitch(t *testing.T) {
	names := func() map[string]bool {
		out := map[string]bool{}
		for _, tool := range toolDefinitions() {
			out[tool.Name] = true
		}
		return out
	}

	t.Setenv("OPEN_COMPUTER_USE_CHANNELS", "")
	all := names()
	for _, tool := range []string{"click", "click_xy", "drag_xy", "press_key", "get_screenshot"} {
		if !all[tool] {
			t.Fatalf("默认应当三条通道全开，缺 %s", tool)
		}
	}

	t.Setenv("OPEN_COMPUTER_USE_CHANNELS", "a11y,keyboard")
	without := names()
	for _, gone := range []string{"click_xy", "drag_xy", "get_screenshot"} {
		if without[gone] {
			t.Fatalf("关掉 gui 通道后 %s 不该还在 tools/list 里", gone)
		}
	}
	for _, kept := range []string{"click", "get_app_state", "press_key", "list_apps"} {
		if !without[kept] {
			t.Fatalf("关掉 gui 不该波及 %s", kept)
		}
	}

	// 硬调也要拒，而且要说清是**通道被关了**，不是工具不存在。
	service := newService()
	result := service.callTool("click_xy", map[string]any{"app": "x", "x": 1.0, "y": 2.0})
	if !result.IsError || !strings.Contains(result.Content[0].Text, "channel, which is disabled") {
		t.Fatalf("被关通道的工具硬调要说清原因: %#v", result)
	}
}

// 像素比对是**独立于树**的第二个信号。两条合起来才有强弱之分。
//
// 起因是实测过的一次误判：ctrl+s 之后树字节不变，工具断言"送达但被忽略"，
// 而文件其实存下来了；agent 因此多花截图 + 点 Save 两步自证。
// 树看不见整类效果（格式改动、文件状态、画布像素），所以"树没变"推不出
// "没生效"——但配上"屏幕也没变"就推得出。
func TestPixelEvidenceStrengthensTheUnchangedVerdict(t *testing.T) {
	changed := strings.Join(unchangedNotes(true, pixelsChanged, false), " ")
	if !strings.Contains(changed, "SCREEN DID change") ||
		!strings.Contains(changed, "Do NOT treat this as a failure") {
		t.Fatalf("树没变但屏幕变了，必须明说这不是失败: %s", changed)
	}

	identical := strings.Join(unchangedNotes(true, pixelsIdentical, false), " ")
	if !strings.Contains(identical, "STRONG evidence") ||
		!strings.Contains(identical, "Two independent signals agree") {
		t.Fatalf("两个信号都说没变，才算强证据: %s", identical)
	}
	// 送达没被单独确认时，结论要更弱一档——不能把两种不确定说成一种。
	weaker := strings.Join(unchangedNotes(false, pixelsIdentical, false), " ")
	if !strings.Contains(weaker, "delivery was not separately verified") {
		t.Fatalf("送达未确认时结论要更弱: %s", weaker)
	}

	// 比不了的时候**不许假装比过**，退回原来的弱措辞。
	unknown := strings.Join(unchangedNotes(true, pixelsUnknown, false), " ")
	if !strings.Contains(unknown, "WEAK evidence, not proof of failure") {
		t.Fatalf("像素比不了时应退回弱措辞: %s", unknown)
	}

	for _, c := range []struct {
		notes []string
		want  pixelVerdict
	}{
		{[]string{"[pixels] The window is pixel-identical to before this action: nothing on screen changed at all."}, pixelsIdentical},
		{[]string{"[pixels] 4.1% of the window changed on screen. Changes are concentrated in {1,2,3,4}."}, pixelsChanged},
		{[]string{"[pixels] The window changed size or position during this action"}, pixelsChanged},
		{[]string{"[a11y][semantic] something"}, pixelsUnknown},
		{nil, pixelsUnknown},
	} {
		if got := readPixelVerdict(c.notes); got != c.want {
			t.Fatalf("readPixelVerdict(%v) = %v, want %v", c.notes, got, c.want)
		}
	}
}

// 选择器的第二身份来源：description。
//
// 优先级照 Playwright 的 selector generator——它给 role+accessible-name 打 100 分，
// 给 label / alt-text 打 140–160（分数越低越优先），都远好于 nth= 的 10000 分。
// 我们的 element_index 就是 nth，是它排名倒数第二的定位方式。
//
// 加 description 这一级是实测决定的：七个应用 704 个节点里，只按 role+name 能唯一
// 指认的占 55%，加上 description 之后到 66%；GIMP 上从 57 涨到 114（翻一倍）。
func TestSelectorFallsBackToDescription(t *testing.T) {
	snapshot := &appSnapshot{Elements: []elementRecord{
		{Index: 3, ControlType: "push button", Name: "", Description: "Go back"},
		{Index: 4, ControlType: "push button", Name: "Save", Description: "Save the current file"},
		{Index: 9, ControlType: "push button", Name: "", Description: "Go forward"},
	}}

	// 没有名字的按钮，靠 description 指认。
	record, err := lookupElement(snapshot, `push button "Go back"`)
	if err != nil || record.Index != 3 {
		t.Fatalf("应当按 description 命中 3：%v %v", record, err)
	}

	// **name 命中时不得被 description 稀释成歧义。**
	// "Save" 既是 4 号的 name，也出现在它自己的 description 里；如果两级混在一起，
	// 一个精确的名字命中就会被一堆描述命中拖成"歧义"。
	record, err = lookupElement(snapshot, `push button "Save"`)
	if err != nil || record.Index != 4 {
		t.Fatalf("name 命中优先，不该报歧义：%v %v", record, err)
	}

	// description 这一级同样要能报歧义，而不是静默挑一个。
	ambiguous := &appSnapshot{Elements: []elementRecord{
		{Index: 1, ControlType: "push button", Description: "Close"},
		{Index: 2, ControlType: "menu item", Description: "Close"},
	}}
	if _, err := lookupElement(ambiguous, `"Close"`); err == nil ||
		!strings.Contains(err.Error(), "ambiguous") {
		t.Fatalf("description 命中多个也要报歧义：%v", err)
	}
}

// 选择器尾部的约束**必须真的生效或真的报错**，不能静默丢弃。
//
// 这是修一个实测出来的 bug：parseSelector 原来取到 name 就 return，
// 末引号之后的内容从头到尾没人看。实测四条全部被吞：
//
//	push button "Save" [checked]              -> role="push button" name="Save" ok=true
//	push button "OK" >> nth=0                 -> role="push button" name="OK"   ok=true
//	menu item "Ruler" (12)                    -> role="menu item"   name="Ruler" ok=true
//	push button "Save" and then some garbage  -> role="push button" name="Save" ok=true
//
// 调用方以为自己加了限定，拿到的却是不带限定的结果——工具在骗 agent。
func TestSelectorPredicatesApplyOrFailLoudly(t *testing.T) {
	snapshot := &appSnapshot{Elements: []elementRecord{
		{Index: 1, ControlType: "check menu item", Name: "Ruler", States: " [checked]"},
		{Index: 2, ControlType: "check menu item", Name: "Ruler"},
		{Index: 5, ControlType: "toggle button", Name: "Menu", Description: "View options"},
		{Index: 6, ControlType: "toggle button", Name: "Menu", Description: "Show operations"},
		{Index: 8, ControlType: "push button", Name: "Go", Actions: []string{"click", "press"}},
		{Index: 9, ControlType: "entry", Placeholder: "Search files"},
		{Index: 11, ControlType: "list item", Name: "Row", States: " [selected focused]"},
	}}

	// 谓词真的把候选筛窄了：不带谓词是歧义，带上就唯一。
	if _, err := lookupElement(snapshot, `check menu item "Ruler"`); err == nil {
		t.Fatal("两个同名 Ruler，本该报歧义")
	}
	record, err := lookupElement(snapshot, `check menu item "Ruler" [checked]`)
	if err != nil || record.Index != 1 {
		t.Fatalf("[checked] 应当筛出 1 号：%v %v", record, err)
	}

	// 快照把多个状态打在**同一个方括号里**，所以一组要拆成多条约束。
	// agent 从行里原样抄下来的就是这个形状。
	if record, err = lookupElement(snapshot, `list item "Row" [selected focused]`); err != nil ||
		record.Index != 11 {
		t.Fatalf("一组多词应当逐词匹配：%v %v", record, err)
	}
	if _, err = lookupElement(snapshot, `list item "Row" [selected expanded]`); err == nil {
		t.Fatal("组里有一个词对不上就不该命中")
	}

	// 同名同角色只能靠 desc 区分——实测 Nautilus 的三个 toggle button "Menu"。
	if record, err = lookupElement(snapshot, `toggle button "Menu" [desc="View options"]`); err != nil ||
		record.Index != 5 {
		t.Fatalf("[desc=…] 应当筛出 5 号：%v %v", record, err)
	}
	// desc 单独用作身份（不给引号名字）也要能走通。
	if record, err = lookupElement(snapshot, `toggle button [desc="Show operations"]`); err != nil ||
		record.Index != 6 {
		t.Fatalf("只给 role + desc 也该唯一：%v %v", record, err)
	}
	if record, err = lookupElement(snapshot, `[placeholder="Search files"]`); err != nil ||
		record.Index != 9 {
		t.Fatalf("只给 placeholder 也该唯一：%v %v", record, err)
	}
	if record, err = lookupElement(snapshot, `push button "Go" [actions=press]`); err != nil ||
		record.Index != 8 {
		t.Fatalf("[actions=…] 应当命中 8 号：%v %v", record, err)
	}

	// 认不出来的一律报错，且错误里要点名残串——这是这次修的核心。
	for _, selector := range []string{
		`push button "OK" >> nth=0`,
		`menu item "Ruler" (12)`,
		`push button "Save" and then some garbage`,
		`push button "Save" [disabled]`,
		`push button "Save" [level=2]`,
		`push button "Save" [desc=View options]`, // 值没加引号
		`push button "Save" [checked`,            // 括号没闭合
	} {
		_, err := lookupElement(snapshot, selector)
		if err == nil {
			t.Fatalf("%q 应当报错，而不是静默丢弃约束", selector)
		}
		if !strings.Contains(err.Error(), "trailing part I cannot read") &&
			!strings.Contains(err.Error(), "no element matches") {
			t.Fatalf("%q 的错误没说清是尾部读不懂：%v", selector, err)
		}
	}

	// 报错要把支持的写法摆出来，并当场宣告 >> / nth= / :right-of 不存在，
	// 否则模型会照着 Playwright 的记忆自己发明语法。
	_, err = lookupElement(snapshot, `push button "OK" >> nth=0`)
	for _, want := range []string{"[checked]", "desc=", "nth=", ">>", ":right-of"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("错误里缺少 %q：%v", want, err)
		}
	}

	// 谓词把候选筛空时，最可能的原因是状态变了而不是名字抄错，
	// 建议要先说这一条。
	_, err = lookupElement(snapshot, `check menu item "Ruler" [expanded]`)
	if err == nil || !strings.Contains(err.Error(), "may have changed since the snapshot") {
		t.Fatalf("筛空时应当提示状态可能已变：%v", err)
	}
	// 解析结果要回显，调用方才知道自己那串被读成了什么。
	if !strings.Contains(err.Error(), "predicates [expanded]") {
		t.Fatalf("Call log 里没回显解析到的谓词：%v", err)
	}
}

// 名字里带引号或反斜杠时，配对引号必须是**真配对**，不是最后一个引号。
//
// 原来用 strings.LastIndex 找收尾引号，在没有尾部内容时恰好正确；
// 一旦后面跟上 [desc="…"]，整段就会被吞进 name。
func TestSelectorFindsTheRealClosingQuote(t *testing.T) {
	role, name, hasName, rest := parseSelector(`toggle button "Menu" [desc="View options"]`)
	if role != "toggle button" || name != "Menu" || !hasName {
		t.Fatalf("配对引号找错了：role=%q name=%q", role, name)
	}
	if rest != `[desc="View options"]` {
		t.Fatalf("残串不对：%q", rest)
	}

	// 渲染侧 quoted() 会把引号转义成 \"，这里要还原回去。
	_, name, _, rest = parseSelector(`static "say \"hi\"" [focused]`)
	if name != `say "hi"` {
		t.Fatalf("转义还原错了：%q", name)
	}
	if rest != "[focused]" {
		t.Fatalf("带转义时残串不对：%q", rest)
	}

	// desc 的值里带 `]` 也要能闭合到正确的位置。
	preds, err := parsePredicates(`[desc="a] b"]`)
	if err != nil || len(preds) != 1 || preds[0].value != "a] b" {
		t.Fatalf("引号内的 ] 不该当成收尾：%v %v", preds, err)
	}
}

// 这条通道是查 Playwright 时反推出来的。它的 aria snapshot 只渲染一小撮语义属性，
// "查任意样式属性"是 toHaveCSS 这类**断言**的事——快照是摘要，断言是精确查询，
// 两者不必是同一套字段。顺着这条思路回头查 AT-SPI，才发现
// Atspi.Text.get_default_attributes() 一直能读到 justification / size / weight。
//
// **此前记进文档的"改段落对齐后 a11y 树字节不变"是错的**：树不变是因为我们
// 没取这些字段，不是信息不存在。真机验证 ctrl+r 报出 `justification: left -> right`。
func TestTextAttributeChanges(t *testing.T) {
	before := &appSnapshot{Elements: []elementRecord{
		{Index: 26, RuntimeID: []int{0, 0, 1}, ControlType: "paragraph",
			TextAttributes: map[string]string{"justification": "left", "weight": "400"}},
		{Index: 49, RuntimeID: []int{0, 0, 2}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "64764,64764,64764"}},
	}}
	after := &appSnapshot{Elements: []elementRecord{
		{Index: 26, RuntimeID: []int{0, 0, 1}, ControlType: "paragraph",
			TextAttributes: map[string]string{"justification": "right", "weight": "400"}},
		{Index: 49, RuntimeID: []int{0, 0, 2}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "64764,64764,64764"}},
	}}

	changes := textAttributeChanges(before, after)
	if len(changes) != 1 {
		t.Fatalf("只有段落变了，应当只报一条：%v", changes)
	}
	if !strings.Contains(changes[0], "justification: left -> right") {
		t.Fatalf("要说出什么变成了什么：%s", changes[0])
	}
	// 没变的属性不许混进来。
	if strings.Contains(changes[0], "weight") {
		t.Fatalf("没变的属性不该出现：%s", changes[0])
	}

	// 回归：第一版按 role+name 配对，而 LibreOffice 侧栏有四个无名 `text` 节点，
	// key 全撞成 `text\x00""`，于是某个节点的悬停高亮被张冠李戴到别人头上，
	// **连空操作都报出"格式变了"**。按 runtimeId 路径配对就没有这个问题。
	collide := &appSnapshot{Elements: []elementRecord{
		{Index: 1, RuntimeID: []int{0, 1}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "a"}},
		{Index: 2, RuntimeID: []int{0, 2}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "b"}},
	}}
	same := &appSnapshot{Elements: []elementRecord{
		{Index: 1, RuntimeID: []int{0, 1}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "a"}},
		{Index: 2, RuntimeID: []int{0, 2}, ControlType: "text",
			TextAttributes: map[string]string{"bg-color": "b"}},
	}}
	if got := textAttributeChanges(collide, same); len(got) != 0 {
		t.Fatalf("同名无名节点不得互相串味：%v", got)
	}

	// 缺一份快照时不许编。
	if got := textAttributeChanges(nil, after); got != nil {
		t.Fatalf("没有动作前的快照就不该报变化：%v", got)
	}
}

// 大输出落盘只回路径——照抄 playwright-mcp 的 --output-mode file|stdout。
//
// 它的解法不是压缩内容，是**把大输出写文件、只回一个路径**，agent 需要细节时
// 自己去读。LibreOffice 的树 17694 字符、VS Code 24731 字符，而 agent 通常
// 只关心其中一两个元素。
func TestOutputSpillsToFileOnlyWhenAskedAndOnlyWhenBig(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("OPEN_COMPUTER_USE_OUTPUT_DIR", dir)

	big := strings.Repeat("a very long tree line\n", 500)

	// 默认 stdout：一个字都不落盘。
	t.Setenv("OPEN_COMPUTER_USE_OUTPUT_MODE", "")
	if got := spillToFile("snapshot", big); got != "" {
		t.Fatalf("默认不该落盘：%s", got[:60])
	}

	// 开了 file 模式，但内容小于阈值时也不落盘——
	// 落盘把一次调用变成两次，对小树是净损失（gedit 的树只有 659 字符）。
	t.Setenv("OPEN_COMPUTER_USE_OUTPUT_MODE", "file")
	if got := spillToFile("snapshot", "short tree"); got != "" {
		t.Fatalf("小输出不该落盘：%s", got)
	}

	replacement := spillToFile("snapshot", big)
	if replacement == "" {
		t.Fatal("大输出应当落盘")
	}
	for _, fragment := range []string{dir, "characters", "lines", "Read that file"} {
		if !strings.Contains(replacement, fragment) {
			t.Fatalf("替代文本要说清写到哪、有多大、怎么读，缺 %q：%s", fragment, replacement)
		}
	}
	// 替代文本必须**远小于**原文，否则这个机制毫无意义。
	if len(replacement) >= len(big)/4 {
		t.Fatalf("替代文本没省下东西：%d vs %d", len(replacement), len(big))
	}

	// 写出去的必须是原文，一个字节都不能少——agent 读它是为了拿全量。
	entries, err := os.ReadDir(dir)
	if err != nil || len(entries) != 1 {
		t.Fatalf("应当只写出一个文件：%v %v", entries, err)
	}
	written, err := os.ReadFile(filepath.Join(dir, entries[0].Name()))
	if err != nil || string(written) != big {
		t.Fatalf("落盘内容与原文不一致")
	}
}

// 落盘失败绝不能让工具失败——原样回文本即可。
func TestSpillFailureFallsBackToInlineText(t *testing.T) {
	t.Setenv("OPEN_COMPUTER_USE_OUTPUT_MODE", "file")
	t.Setenv("OPEN_COMPUTER_USE_OUTPUT_DIR", "/proc/definitely-not-writable")
	if got := spillToFile("snapshot", strings.Repeat("x", 9000)); got != "" {
		t.Fatalf("写不成时应当退回内联，而不是给出一个不存在的路径：%s", got[:60])
	}
}

func TestFindFiltersAreAndedAndReuseTheRenderedLines(t *testing.T) {
	// find 的输出必须**逐字复用**树的渲染行，不能在 Go 里再写一份渲染器。
	// 两份实现迟早漂移，而 agent 只该学一套读法。
	snapshot := &appSnapshot{
		WindowTitle: "Untitled 1",
		Elements: []elementRecord{
			{Index: 3, ControlType: "push button", Name: "Save", States: "enabled focused"},
			{Index: 4, ControlType: "push button", Name: "Save As…", States: "enabled"},
			{Index: 5, ControlType: "toggle button", Name: "Bold", States: "enabled checked"},
			{Index: 6, ControlType: "text", Placeholder: "Search files"},
		},
		TreeLines: []string{
			"  3 push button \"Save\" [enabled focused]",
			"  4 push button \"Save As…\" [enabled]",
			"  5 toggle button \"Bold\" [enabled checked]",
			"  6 text [placeholder=\"Search files\"]",
			"  … 12 more nodes",
		},
	}

	lines := linesByIndex(snapshot)
	if len(lines) != 4 {
		t.Fatalf("行索引应跳过非数字开头的截断提示行，得到 %d 条", len(lines))
	}
	if lines[5] != "  5 toggle button \"Bold\" [enabled checked]" {
		t.Fatalf("行必须原样取自渲染结果，得到 %q", lines[5])
	}

	cases := []struct {
		role, name, text, state string
		want                    []int
	}{
		// role 用子串：agent 写 "button" 时两种 button 都该找到
		{role: "button", want: []int{3, 4, 5}},
		// 多条件是**与**，不是或
		{role: "button", state: "checked", want: []int{5}},
		{name: "save", want: []int{3, 4}},
		// text 要能落到 placeholder 上——同一个搜索框在 GTK 是 placeholder、
		// 在 Electron 是 name，agent 不该被迫知道是哪个
		{text: "search files", want: []int{6}},
		{role: "button", name: "nonexistent", want: nil},
	}
	for _, c := range cases {
		got := []int{}
		for i := range snapshot.Elements {
			if recordMatches(&snapshot.Elements[i], c.role, c.name, c.text, c.state) {
				got = append(got, snapshot.Elements[i].Index)
			}
		}
		if len(got) != len(c.want) {
			t.Fatalf("查询 %+v 期望 %v，得到 %v", c, c.want, got)
		}
		for i := range got {
			if got[i] != c.want[i] {
				t.Fatalf("查询 %+v 期望 %v，得到 %v", c, c.want, got)
			}
		}
	}
}

func TestVerifyReportsWhatItActuallySawNotJustFailure(t *testing.T) {
	// "断言失败"没有信息量。有信息量的是"期望 checked，实际 [enabled focused]"
	// ——agent 拿到后者才知道下一步该做什么。
	record := &elementRecord{Index: 5, ControlType: "toggle button", Name: "Bold", States: "enabled focused"}

	ok, observed := verifyGoal{state: "checked"}.check(record, nil)
	if ok {
		t.Fatal("元素没有 checked，断言不该通过")
	}
	if !strings.Contains(observed, "enabled focused") {
		t.Fatalf("失败时必须回报实际观测到的状态，得到 %q", observed)
	}

	// 取反语法
	if ok, _ := (verifyGoal{state: "!checked"}).check(record, nil); !ok {
		t.Fatal("!checked 对一个未选中的元素应当通过")
	}

	// exists:false 是"等对话框消失"的写法：元素找不到才算通过
	no := false
	if ok, _ := (verifyGoal{exists: &no}).check(nil, nil); !ok {
		t.Fatal("元素不存在时 exists:false 应当通过")
	}
	if ok, _ := (verifyGoal{exists: &no}).check(record, nil); ok {
		t.Fatal("元素还在时 exists:false 不该通过")
	}

	// 缺省超时下，断言不存在的元素要给出可行动的理由，而不是空的失败
	if ok, observed := (verifyGoal{state: "checked"}).check(nil, nil); ok || observed == "" {
		t.Fatalf("找不到元素时要给出理由，得到 ok=%v observed=%q", ok, observed)
	}
}

func TestFindAndVerifySkipTheScreenshot(t *testing.T) {
	// 查询与断言都不是"观测"，不该付视觉 token——基线里一次观测的视觉部分
	// 是 5120 token，而 verify 会轮询，带图的话一次断言能烧掉十几次观测的钱。
	for _, name := range []string{"find", "verify"} {
		definition := findToolDefinition(t, name)
		if definition.Annotations == nil || definition.Annotations["readOnlyHint"] != true {
			t.Fatalf("%s 不改变界面，必须标成只读", name)
		}
	}
	if !strings.Contains(findToolDefinition(t, "find").Description, "no screenshot") {
		t.Fatal("find 的描述要写明不回截图，否则 agent 会以为它替代了 get_app_state")
	}
	if !strings.Contains(findToolDefinition(t, "find").Description, "does NOT make the machine faster") {
		t.Fatal("find 必须如实说明它省的是上下文而不是机器成本")
	}
}

func TestVerifyDoesNotDoubleWrapTheStates(t *testing.T) {
	// 树里渲染出来的 states 自带方括号，再包一层就成了 `[ [enabled]]`。
	record := &elementRecord{Index: 1, ControlType: "push button", Name: "Search", States: " [has-click-action]"}
	_, observed := verifyGoal{state: "checked"}.check(record, nil)
	if strings.Contains(observed, "[ [") || strings.Contains(observed, "]]") {
		t.Fatalf("states 不该被二次加括号，得到 %q", observed)
	}
}

func TestVerifyAcceptsNumericIndicesNotJustSelectors(t *testing.T) {
	// 实测踩到过：verify 走 lookupBySelectorFor，传下标 "62" 会连报 6 轮
	// "no element matches the selector" —— 元素一直在，断言却报不存在。
	// 断言工具给假阴性，比没有断言更糟。
	snapshot := &appSnapshot{Elements: []elementRecord{
		{Index: 62, ControlType: "text", Value: "baseline-marker", States: "[focused]"},
	}}
	record, err := lookupElementFor(snapshot, "62", "", "verify")
	if err != nil || record == nil {
		t.Fatalf("数字下标必须能解析，得到 record=%v err=%v", record, err)
	}
	if ok, _ := (verifyGoal{textContains: "baseline"}).check(record, nil); !ok {
		t.Fatal("解析到的记录应当满足 text_contains")
	}
}

func TestCoordinateNotesAreAlwaysWindowRelative(t *testing.T) {
	// 实测踩到过：Nautilus 图标在树里是 {256,76,78,68}（中心 295,110），
	// 合成右键的 Note 却报 (384,159)——差的正是窗口原点 (89,49)。
	// agent 照那个数去 click_xy 会偏出整整一个窗口原点。
	// click_xy 的 Note 早就写死了"window-relative"这个约定，其余几处必须一致。
	if !strings.Contains(linuxRuntimeScript, "def window_relative(") {
		t.Fatal("需要一个把绝对坐标换回窗口相对的转换函数")
	}
	for _, fragment := range []string{
		// 钉坐标空间的声明本身，不钉措辞的换行位置——后者一改文案就假报警。
		"right-click at ({:.0f}, {:.0f}) in window-relative pixels",
		"Synthesized a coordinate click at ({:.0f}, {:.0f}) in window-relative ",
	} {
		if !strings.Contains(linuxRuntimeScript, fragment) {
			t.Fatalf("坐标 Note 必须声明自己的坐标空间：缺 %q", fragment)
		}
	}
	// 每一处报坐标的地方都要经过转换，不能直接打印 screen_point 的返回值
	if strings.Contains(linuxRuntimeScript, `"{}".format(action, x, y, UNVERIFIED_SYNTHESIS)`) {
		t.Fatal("menu 兜底仍在直接打印屏幕绝对坐标")
	}
}

func TestContextMenuPrefersTheKeyboardRouteOverSyntheticRightClick(t *testing.T) {
	// 实测：合成右键（button 3）在本机 Nautilus 上 100% 开不出上下文菜单。
	// 用 xdotool 绕开本项目直接发同样的右键一样失败，拆成 mousedown/mouseup
	// 也失败；而同一位置的左键立刻生效（图标变 [focused]、状态栏出现 selected）
	// ——所以既不是坐标错也不是焦点问题，是 button 3 这条路不通。
	// Shift+F10 一次开出 11 个 menu item，它本来就是无障碍标准的入口。
	if !strings.Contains(linuxRuntimeScript, `send_key("shift+F10")`) {
		t.Fatal("菜单兜底必须先走 Shift+F10 这条无障碍路线")
	}
	shiftAt := strings.Index(linuxRuntimeScript, `send_key("shift+F10")`)
	rightAt := strings.Index(linuxRuntimeScript, `send_mouse_click(x, y, "right", 1)`)
	if rightAt < 0 || shiftAt > rightAt {
		t.Fatal("Shift+F10 必须排在合成右键之前")
	}
	// 但**不能删掉**合成右键：它在别的工具包上是通的，
	// 用一个应用的证据去否掉另一些应用的唯一出路是过度归纳。
	if rightAt < 0 {
		t.Fatal("合成右键要保留作为最后兜底")
	}
}

func TestActionsReResolveGeometryAtActionTime(t *testing.T) {
	// Playwright 的 locator 每次动作重新解析。我们其实早就在 find_element 里
	// 解析出了活节点，却仍拿 Go 缓存的旧记录去算坐标——同一次调用里两套事实。
	// 实测：VS Code 里由外部收起侧栏后，click(element_index=21) 合成在 (572,38)，
	// 正是缓存框 {547,28,50,19} 的中心，而界面那时已经重排。
	if !strings.Contains(linuxRuntimeScript, "def current_geometry(") {
		t.Fatal("动作路径必须在解析出活节点之后重新取一次几何")
	}
	if !strings.Contains(linuxRuntimeScript, "element_record, moved_note = current_geometry(") {
		t.Fatal("重算的几何必须真的被后续动作使用，而不是算完丢掉")
	}
	// 位移发生了就必须说——这是 agent 判断"我读到的坐标还能不能用"的唯一依据
	if !strings.Contains(linuxRuntimeScript, "The element MOVED since the snapshot") {
		t.Fatal("元素移动过要如实告知，否则 agent 会继续用快照里的旧坐标")
	}
	// 按元素定位的动作都要把**快照当时**的窗口矩形送下去：少了它，运行时只能
	// 拿"现在的窗口位置"解释"快照当时的元素框"，两个坐标空间一混就会凭空报警。
	for _, fragment := range []string{
		`Tool: "invoke_element_action", App: app, Element: record, Action: action, WindowBounds: snapshot.WindowBounds`,
		`Tool: "scroll", App: app, Element: record, Direction: normalized, Pages: pages, WindowBounds: snapshot.WindowBounds`,
		`Tool: "set_value", App: app, Element: record, Value: value, WindowBounds: snapshot.WindowBounds`,
	} {
		if !strings.Contains(mainGoSource(t), fragment) {
			t.Fatalf("按元素定位的动作缺少快照窗口矩形: %s", fragment)
		}
	}
}

func mainGoSource(t *testing.T) string {
	t.Helper()
	data, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatalf("读不到 main.go: %v", err)
	}
	return string(data)
}
