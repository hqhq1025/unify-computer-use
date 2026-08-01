package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"
)

var version = "0.3.0"

var clickMethodValues = []string{"auto", "accessibility", "app_post", "sky_click", "global"}

//go:embed runtime.py
var linuxRuntimeScript string

const serverInstructions = "Computer Use tools let you interact with Linux desktop apps by performing UI actions.\n\nBegin by calling `get_app_state` every turn you want to use Computer Use to get the latest state before acting. The available tools are list_apps, get_app_state, get_screenshot, click, invoke_element_action, scroll, drag, type_text, press_key, and set_value.\n\nEach line of the accessibility tree reads: `<index> <role> \"<name>\" [<states>] [desc=\"…\"] [placeholder=\"…\"] [actions=a,b] {x,y,width,height}: \"<value>\"`. Every free-text field is quoted, because names themselves can contain colons and spaces; bracketed groups and the trailing value are omitted when empty. The braces are the element's rectangle in window-relative pixels — the same coordinate space as the screenshot and as the x/y arguments of click_xy and drag_xy. The leading integer is the element_index you pass to the accessibility-channel tools.\n\nEvery tool declares a CHANNEL in the first words of its description, and the channel tells you how the target was addressed and therefore how to verify the result. There are three. ACCESSIBILITY addresses a target by element_index from the tree (click, invoke_element_action, set_value): the element is identified, not guessed, and it usually does not steal focus. GUI addresses a target by window-relative pixel coordinates (click_xy, drag_xy, get_screenshot): no element is identified at all, whatever sits under the point receives the input. KEYBOARD addresses nothing — the input goes to whichever widget currently holds focus inside the window (press_key, scroll, and type_text when its accessibility write does not land). Every action note is tagged with both its addressing channel ([a11y], [gui], [keyboard]) and its execution path ([semantic] AT-SPI call, [synthesis] XTEST). The combination [a11y][synthesis] is common and healthy: the target came from the tree, the click was synthesized at the tree's own coordinates.\n\nThe channels are COMPLEMENTARY, not interchangeable, and neither substitutes for the other. The tree is the ACTIONABLE one: it hands you element_index values you can act on, and control semantics a picture cannot convey — which of four unlabelled spin buttons is 'Position Y' is stated in that element's description. Prefer it whenever the target has an index. The screenshot is the VERIFIABLE one: many effects never reach the tree. Measured on LibreOffice Impress, applying right alignment and saving the file both took effect while the tree stayed byte-identical, so two actions that had in fact worked were reported as 'nothing observably changed'; and dragging a title from 0.76cm to 15.00cm left that element's Frame in the tree unchanged. Treat 'the tree did not change' as weak evidence of failure, never as proof. get_app_state therefore returns a screenshot alongside the tree, and the GUI-channel tools always return one. Use get_screenshot when you want the image WITHOUT paying for the tree. Do not assume the tree is always the cheaper channel either: measured here a window screenshot runs about a thousand tokens, more than a small app's tree (gedit ~350) but LESS than a content-rich one (a file manager tree ~2100).\n\nCoordinates are one consistent space across everything: Frame values in the tree, screenshot pixels, and the x/y arguments of click_xy and drag_xy are all window-relative, so a point read off the image can be passed straight to click_xy with no conversion.\n\nElements that expose an accessibility action are marked `[has-click-action]`, computed from the exact action click would invoke. Read it literally: the element HAS an action, NOT that the action works. AT-SPI actions on Linux routinely report success while doing nothing, so confirm from the returned state instead of trusting the call. An element with no `[has-click-action]` marker exposes no usable accessibility action at all; pass its element_index to click anyway — the coordinates then come from the tree, which is still better than guessing them.\n\nInput synthesis on Linux is global: it lands on whichever window currently holds focus, not on the app named in the call. Tools that synthesize therefore bring the target window to the foreground first, and fail rather than deliver input somewhere else. Expect press_key, scroll, drag_xy and click_xy to steal focus, and prefer set_value or element-targeted click when you need to avoid that. Linux actions use AT-SPI2 semantic actions and editable-text APIs first; coordinate mouse and key synthesis are best-effort fallbacks and are not a universal Wayland background input model.\n\nBrowsers are NOT handled by these tools. Chrome and Chromium are driven by a separate control plane (Playwright/browser-use over CDP), so do not call get_app_state, click, or type_text against them here — their accessibility tree is disabled by default and you will burn turns on an app you cannot see. If a task needs the browser, hand it to the browser control plane. Everything outside the browser — LibreOffice, VS Code, GIMP, VLC, Thunderbird, the file manager, the terminal — belongs to these tools. The handoff point between the two planes is the filesystem: a browser download lands in ~/Downloads and is then opened with these tools."

type toolDefinition struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Annotations map[string]any `json:"annotations,omitempty"`
	InputSchema map[string]any `json:"inputSchema"`
}

type contentItem struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
}

type toolCallResult struct {
	Content []contentItem `json:"content"`
	IsError bool          `json:"isError"`
}

func textResult(text string, isError bool) toolCallResult {
	return toolCallResult{Content: []contentItem{{Type: "text", Text: text}}, IsError: isError}
}

type appDescriptor struct {
	Name             string `json:"name"`
	BundleIdentifier string `json:"bundleIdentifier,omitempty"`
	PID              int    `json:"pid"`
}

type frame struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

func (f frame) renderedLocalFrame() string {
	return fmt.Sprintf("{{x: %.0f, y: %.0f, width: %.0f, height: %.0f}}", f.X, f.Y, f.Width, f.Height)
}

type elementRecord struct {
	Index                int               `json:"index"`
	RuntimeID            []int             `json:"runtimeId,omitempty"`
	AutomationID         string            `json:"automationId,omitempty"`
	Name                 string            `json:"name,omitempty"`
	ControlType          string            `json:"controlType,omitempty"`
	LocalizedControlType string            `json:"localizedControlType,omitempty"`
	ClassName            string            `json:"className,omitempty"`
	Value                string            `json:"value,omitempty"`
	NativeWindowHandle   int64             `json:"nativeWindowHandle,omitempty"`
	Frame                *frame            `json:"frame,omitempty"`
	Actions              []string          `json:"actions,omitempty"`
	States               string            `json:"states,omitempty"`
	Description          string            `json:"description,omitempty"`
	TextAttributes       map[string]string `json:"textAttributes,omitempty"`
	Placeholder          string            `json:"placeholder,omitempty"`
}

// elementRef 是一个元素的稳定编号及其失效条件。
// 照 Playwright 的 ariaSnapshot.ts：role 或 name 变了就重新发号，
// 因为它已经不是"同一个东西"了。
type elementRef struct {
	Index int    `json:"index"`
	Role  string `json:"role"`
	Name  string `json:"name"`
}

type appSnapshot struct {
	App                 appDescriptor         `json:"app"`
	WindowTitle         string                `json:"windowTitle,omitempty"`
	WindowBounds        *frame                `json:"windowBounds,omitempty"`
	ScreenshotPNGBase64 string                `json:"screenshotPngBase64,omitempty"`
	TreeLines           []string              `json:"treeLines,omitempty"`
	FocusedSummary      string                `json:"focusedSummary,omitempty"`
	SelectedText        string                `json:"selectedText,omitempty"`
	Elements            []elementRecord       `json:"elements,omitempty"`
	Refs                map[string]elementRef `json:"refs,omitempty"`

	// 下面两个字段由 Go 侧在缓存时补上，不来自 Python。
	// 快照有多旧是**我们特有的必需信息**：浏览器里 DOM 变化通常伴随可观测事件，
	// 桌面上快照可能已经陈旧几十秒而 agent 毫无察觉。
	capturedAt time.Time
	sourceApp  string
}

func (s *appSnapshot) renderedText() string {
	if s == nil {
		return ""
	}
	appRef := s.App.BundleIdentifier
	if appRef == "" {
		appRef = s.App.Name
	}
	title := s.WindowTitle
	if strings.TrimSpace(title) == "" {
		title = s.App.Name
	}

	lines := []string{
		fmt.Sprintf("App=%s (pid %d)", appRef, s.App.PID),
		fmt.Sprintf("Window: %q, App: %s.", title, s.App.Name),
	}
	lines = append(lines, s.TreeLines...)
	if strings.TrimSpace(s.SelectedText) != "" {
		lines = append(lines, "", fmt.Sprintf("Selected text: [%s]", s.SelectedText))
	} else if strings.TrimSpace(s.FocusedSummary) != "" {
		lines = append(lines, "", fmt.Sprintf("The focused UI element is %s.", s.FocusedSummary))
	}
	return strings.Join(lines, "\n")
}

// 大输出落盘、响应里只回路径——照抄 playwright-mcp 的 `--output-mode file|stdout`：
//
//	--output-mode <mode>   whether to save snapshots, console messages, network
//	                       logs to a file or to the standard output.
//	                       Can be "file" or "stdout". Default is "stdout".
//
// 它的响应里长这样：
//
//	### Snapshot
//	[Snapshot](.playwright-cli/page-2026-02-14T19-22-42-679Z.yml)
//
// **它的解法不是压缩内容，是把大输出写文件、只回一个路径**，agent 需要细节时
// 自己去读。对我们尤其有价值：LibreOffice 的树 17694 字符、VS Code 24731 字符，
// 而 agent 通常只关心其中一两个元素。
//
// 默认仍然是 stdout，有两个原因，第二个更硬：
//  1. 落盘把"一次调用拿到全部信息"变成"两次调用"，对小树是净损失
//     （gedit 的树只有 659 字符，写出去再读回来纯属折腾）。
//  2. **它要求 agent 有读文件的能力。** 我们跑 OSWorld 时刻意禁掉了
//     Bash/Read/Write（否则 agent 会绕开 GUI 直接改文件），那种配置下
//     落盘模式会让树**根本读不到**。所以这是一个部署方决定的开关，
//     不能默认打开。
func outputMode() string {
	mode := strings.ToLower(strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_OUTPUT_MODE")))
	if mode == "file" {
		return "file"
	}
	return "stdout"
}

func outputDir() string {
	if dir := strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_OUTPUT_DIR")); dir != "" {
		return dir
	}
	return filepath.Join(os.TempDir(), "open-computer-use")
}

// outputThreshold 小于这个字符数的输出不落盘。
// 落盘把一次调用变成两次，对小树是净损失——gedit 的树只有 659 字符，
// 写出去再读回来纯属折腾。
func outputThreshold() int {
	if raw := strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_OUTPUT_THRESHOLD")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 {
			return n
		}
	}
	return 4000
}

// spillToFile 把大文本写进输出目录，返回给 agent 看的替代文本。
// 写不成就返回空串——**落盘失败绝不能让工具失败**，原样回文本即可。
func spillToFile(kind, text string) string {
	if outputMode() != "file" || len(text) < outputThreshold() {
		return ""
	}
	dir := outputDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return ""
	}
	name := fmt.Sprintf("%s-%d.txt", kind, time.Now().UnixNano())
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		return ""
	}
	head := text
	if idx := strings.Index(head, "\n"); idx >= 0 {
		if second := strings.Index(head[idx+1:], "\n"); second >= 0 {
			head = head[:idx+1+second]
		}
	}
	return fmt.Sprintf("%s\n\n[%s written to %s — %d characters, %d lines. "+
		"Read that file when you need the detail; it is the same content this "+
		"response would otherwise have inlined. If you have no way to read files, "+
		"ask the operator to unset OPEN_COMPUTER_USE_OUTPUT_MODE.]",
		head, kind, path, len(text), strings.Count(text, "\n")+1)
}

func (s *appSnapshot) result() toolCallResult {
	return s.resultWithNotes(nil)
}

// resultWithTreeOverride 用给定的行替代 treeLines 渲染，用于增量视图。
func (s *appSnapshot) resultWithTreeOverride(notes, lines []string) toolCallResult {
	if s == nil {
		return s.resultWithNotes(notes)
	}
	clone := *s
	clone.TreeLines = lines
	return clone.resultWithNotes(notes)
}

// resultWithNotes 在 accessibility tree 前面先说清楚这次动作实际做了什么、
// 结果有没有被确认过。只返回一棵新树很容易被读成"动作成功了"，但它只是快照。
func (s *appSnapshot) resultWithNotes(notes []string) toolCallResult {
	return s.textWithNotes(s.renderedText(), notes)
}

// textWithNotes 走和整棵树完全相同的出口：诊断在前、正文在后、超阈值落盘。
// find 的输出必须和 get_app_state 共用这条路径，否则"树太大"这个问题
// 会在 find 上原样复发一遍——命中 300 个元素时它一样是一份大输出。
func (s *appSnapshot) textWithNotes(text string, notes []string) toolCallResult {
	if len(notes) > 0 {
		lines := make([]string, 0, len(notes)+1)
		for _, note := range notes {
			lines = append(lines, "Note: "+note)
		}
		lines = append(lines, "", text)
		text = strings.Join(lines, "\n")
	}
	if spilled := spillToFile("snapshot", text); spilled != "" {
		text = spilled
	}
	result := toolCallResult{
		Content: []contentItem{{Type: "text", Text: text}},
	}
	if s != nil && s.ScreenshotPNGBase64 != "" {
		result.Content = append(result.Content, contentItem{
			Type:     "image",
			Data:     s.ScreenshotPNGBase64,
			MimeType: "image/png",
		})
	}
	return result
}

type linuxRequest struct {
	Tool         string         `json:"tool"`
	App          string         `json:"app,omitempty"`
	Element      *elementRecord `json:"element,omitempty"`
	X            *float64       `json:"x,omitempty"`
	Y            *float64       `json:"y,omitempty"`
	FromX        *float64       `json:"from_x,omitempty"`
	FromY        *float64       `json:"from_y,omitempty"`
	ToX          *float64       `json:"to_x,omitempty"`
	ToY          *float64       `json:"to_y,omitempty"`
	ClickCount   int            `json:"click_count,omitempty"`
	MouseButton  string         `json:"mouse_button,omitempty"`
	ClickMethod  string         `json:"click_method,omitempty"`
	Action       string         `json:"action,omitempty"`
	Direction    string         `json:"direction,omitempty"`
	Pages        float64        `json:"pages,omitempty"`
	Text         string         `json:"text,omitempty"`
	Key          string         `json:"key,omitempty"`
	Value        string         `json:"value,omitempty"`
	WindowBounds *frame         `json:"windowBounds,omitempty"`
	TextLimit    any            `json:"text_limit,omitempty"`
	MaxTreeNodes int            `json:"max_tree_nodes,omitempty"`
	MaxTreeDepth int            `json:"max_tree_depth,omitempty"`
	Prune        *bool          `json:"prune,omitempty"`
	Boxes        *bool          `json:"boxes,omitempty"`
	// nil = 按运行时的默认策略。find / verify 显式传 false 省掉视觉 token。
	IncludeScreenshot *bool `json:"includeScreenshot,omitempty"`
	// 上一份快照的 路径 -> (编号, role, name)。原样带给运行时，让编号跨快照存活。
	KnownRefs map[string]elementRef `json:"knownRefs,omitempty"`
}

type textLimit struct {
	max   bool
	count int
}

func (limit textLimit) runtimeValue() any {
	if limit.max {
		return "max"
	}
	return limit.count
}

type linuxResponse struct {
	OK       bool         `json:"ok"`
	Text     string       `json:"text,omitempty"`
	Error    string       `json:"error,omitempty"`
	Notes    []string     `json:"notes,omitempty"`
	Snapshot *appSnapshot `json:"snapshot,omitempty"`
}

type service struct {
	snapshots map[string]*appSnapshot
}

func newService() *service {
	return &service{snapshots: map[string]*appSnapshot{}}
}

// errorContextEnabled 失败时落一份现场文件，默认**开**。
//
// 和 --output-mode file 相反的默认值，理由也相反：那个改变的是成功路径上每次
// 调用的形态（会把"一次拿到全部"变成"两次"），这个只在失败时触发，而失败时
// 没有任何别的手段能事后复盘——响应里只剩一行错误，现场早就没了。
func errorContextEnabled() bool {
	return !strings.EqualFold(
		strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_ERROR_CONTEXT")), "off")
}

// writeErrorContext 在失败时把**失败当时**的现场写到磁盘，返回可读的说明。
//
// 照 Playwright 的 error-context.md：失败时它会连同页面快照一起落盘，
// 因为一行错误信息没法复盘。桌面上更是如此——界面下一秒就变了，
// 而我们连"当时树长什么样"都没有留。
//
// 现场是**重新抓的**，不是缓存里那份：失败往往正是因为界面已经不是快照里的
// 样子了，拿缓存去当现场等于把要查的东西本身丢掉。代价只在失败路径上付。
//
// 抓现场自己也可能失败（应用已经退出、a11y 总线断了）。那种情况下**不做任何
// 补救**，直接返回空串——一个诊断设施不该在诊断失败时再制造一层新错误。
func (s *service) writeErrorContext(app, tool, message string) string {
	if !errorContextEnabled() || strings.TrimSpace(app) == "" {
		return ""
	}
	noRefs := false
	response, err := runPython(linuxRequest{
		Tool: "get_app_state", App: app, Boxes: &noRefs,
	})
	if err != nil || response == nil || !response.OK || response.Snapshot == nil {
		return ""
	}
	dir := outputDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return ""
	}
	stamp := time.Now().UnixNano()
	snapshot := response.Snapshot
	var body strings.Builder
	fmt.Fprintf(&body, "# error context\n\ntool: %s\napp: %s\ncaptured: %s\n\n",
		tool, app, time.Now().Format(time.RFC3339Nano))
	fmt.Fprintf(&body, "## error\n\n%s\n\n", message)
	fmt.Fprintf(&body, "## window\n\n%s\n\n", snapshot.WindowTitle)
	fmt.Fprintf(&body, "## tree at failure time\n\n%s\n", snapshot.renderedText())
	textPath := filepath.Join(dir, fmt.Sprintf("error-context-%d.md", stamp))
	if err := os.WriteFile(textPath, []byte(body.String()), 0o644); err != nil {
		return ""
	}
	parts := []string{textPath}
	if snapshot.ScreenshotPNGBase64 != "" {
		if raw, decodeErr := base64.StdEncoding.DecodeString(snapshot.ScreenshotPNGBase64); decodeErr == nil {
			shotPath := filepath.Join(dir, fmt.Sprintf("error-context-%d.png", stamp))
			if os.WriteFile(shotPath, raw, 0o644) == nil {
				parts = append(parts, shotPath)
			}
		}
	}
	return fmt.Sprintf(
		"\n\n[error context written to %s \u2014 the tree and screenshot as they were "+
			"AFTER this failure, re-captured rather than taken from the cached "+
			"snapshot, because the failure usually means the UI is no longer what "+
			"the snapshot said. Set OPEN_COMPUTER_USE_ERROR_CONTEXT=off to disable.]",
		strings.Join(parts, " and "))
}

func (s *service) callTool(name string, args map[string]any) toolCallResult {
	result := s.dispatch(name, args)
	// 失败现场统一在这里落盘：错误从十几个地方返回，逐个去加只会漏。
	if result.IsError {
		app := requiredString(args, "app")
		message := ""
		for _, item := range result.Content {
			if item.Type == "text" {
				message += item.Text
			}
		}
		if note := s.writeErrorContext(app, name, message); note != "" {
			result.Content = append(result.Content, contentItem{Type: "text", Text: note})
		}
	}
	return result
}

func (s *service) dispatch(name string, args map[string]any) toolCallResult {
	if !toolIsEnabled(name) {
		// 工具已经从 tools/list 里摘掉了，但硬调仍然要拒——而且要说清是**通道被
		// 关掉了**，不是工具不存在。含糊的报错会让 agent 去猜是不是名字写错了。
		return textResult(fmt.Sprintf(
			"tool %q belongs to the %s channel, which is disabled by OPEN_COMPUTER_USE_CHANNELS. Enabled channels: %s.",
			name, toolChannel[name], strings.Join(sortedChannels(), ", ")), true)
	}
	switch name {
	case "list_apps":
		return s.listApps()
	case "get_screenshot":
		return s.getScreenshot(requiredString(args, "app"))
	case "get_app_state":
		maxTreeNodes, err := optionalPositiveInt(args, "max_tree_nodes")
		if err != nil {
			return textResult(err.Error(), true)
		}
		maxTreeDepth, err := optionalPositiveInt(args, "max_tree_depth")
		if err != nil {
			return textResult(err.Error(), true)
		}
		textLimit, err := optionalTextLimit(args, "text_limit")
		if err != nil {
			return textResult(err.Error(), true)
		}
		return s.getAppState(requiredString(args, "app"), textLimit, maxTreeNodes, maxTreeDepth, optionalBool(args, "prune"), optionalBool(args, "boxes"))
	case "find":
		limit, err := optionalPositiveInt(args, "limit")
		if err != nil {
			return textResult(err.Error(), true)
		}
		return s.find(
			requiredString(args, "app"),
			optionalString(args, "role"),
			optionalString(args, "name"),
			optionalString(args, "text"),
			optionalString(args, "state"),
			intValue(optionalFloat(args, "limit"), func() int {
				if limit != nil {
					return *limit
				}
				return 20
			}()),
		)
	case "verify":
		timeout, err := optionalPositiveInt(args, "timeout_ms")
		if err != nil {
			return textResult(err.Error(), true)
		}
		goal := verifyGoal{
			state:         optionalString(args, "state"),
			valueContains: optionalString(args, "value_contains"),
			textContains:  optionalString(args, "text_contains"),
			exists:        optionalBool(args, "exists"),
		}
		timeoutMS := 5000
		if timeout != nil {
			timeoutMS = *timeout
		}
		return s.verify(requiredString(args, "app"), requiredElementIndex(args), goal, timeoutMS)
	case "click":
		clickMethod, err := parseClickMethod(optionalString(args, "click_method"))
		if err != nil {
			return textResult(err.Error(), true)
		}
		return s.click(
			requiredString(args, "app"),
			requiredElementIndex(args),
			optionalString(args, "element"),
			optionalFloat(args, "x"),
			optionalFloat(args, "y"),
			intValue(optionalFloat(args, "click_count"), 1),
			defaultString(optionalString(args, "mouse_button"), "left"),
			clickMethod,
		)
	case "click_xy":
		return s.clickXY(
			requiredString(args, "app"),
			optionalFloat(args, "x"),
			optionalFloat(args, "y"),
			intValue(optionalFloat(args, "click_count"), 1),
			defaultString(optionalString(args, "mouse_button"), "left"),
		)
	case "invoke_element_action":
		return s.performSecondaryAction(
			requiredString(args, "app"),
			requiredElementIndex(args),
			optionalString(args, "element"),
			requiredString(args, "action"),
		)
	case "scroll":
		return s.scroll(
			requiredString(args, "app"),
			requiredString(args, "direction"),
			requiredElementIndex(args),
			optionalString(args, "element"),
			floatValue(optionalFloat(args, "pages"), 1),
		)
	case "drag_xy":
		return s.dragXY(
			requiredString(args, "app"),
			requiredFloat(args, "from_x"),
			requiredFloat(args, "from_y"),
			requiredFloat(args, "to_x"),
			requiredFloat(args, "to_y"),
		)
	case "type_text":
		return s.typeText(requiredString(args, "app"), requiredString(args, "text"))
	case "press_key":
		return s.pressKey(requiredString(args, "app"), requiredString(args, "key"))
	case "set_value":
		return s.setValue(requiredString(args, "app"), requiredElementIndex(args), optionalString(args, "element"), requiredString(args, "value"))
	default:
		return textResult(fmt.Sprintf("unsupportedTool(%q)", name), true)
	}
}

func (s *service) listApps() toolCallResult {
	response, err := runPython(linuxRequest{Tool: "list_apps"})
	if err != nil {
		return textResult(err.Error(), true)
	}
	if !response.OK {
		return textResult(response.Error, true)
	}
	if strings.TrimSpace(response.Text) == "" {
		response.Text = "No running top-level apps are visible to this Linux runtime."
	}
	return textResult(response.Text, false)
}

func (s *service) getAppState(app string, textLimit *textLimit, maxTreeNodes, maxTreeDepth *int, prune, boxes *bool) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	request := linuxRequest{Tool: "get_app_state", App: app}
	if textLimit != nil {
		request.TextLimit = textLimit.runtimeValue()
	}
	if maxTreeNodes != nil {
		request.MaxTreeNodes = *maxTreeNodes
	}
	if maxTreeDepth != nil {
		request.MaxTreeDepth = *maxTreeDepth
	}
	request.Prune = prune
	request.Boxes = boxes
	snapshot, notes, result := s.refreshSnapshot(app, request)
	if result.IsError {
		return result
	}
	// get_app_state 也要带上诊断：应用可能活着但 a11y 是空壳，
	// 不说明的话 agent 会把"我看不见"误读成"界面是空的"。
	return snapshot.resultWithNotes(notes)
}

// getScreenshot 是 VLM 轨道的唯一入口。a11y 轨（get_app_state 与所有动作工具）
// 一律不带图，避免每次调用都同时付两条轨道的钱。
func (s *service) getScreenshot(app string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	snapshot, _, result := s.refreshSnapshot(app, linuxRequest{Tool: "get_screenshot", App: app})
	if result.IsError {
		return result
	}
	if snapshot.ScreenshotPNGBase64 == "" {
		return textResult("No screenshot is available for this window. On GNOME Wayland the capture may come back blank; use the accessibility tree instead.", true)
	}
	return toolCallResult{Content: []contentItem{
		{Type: "text", Text: fmt.Sprintf("Screenshot of %q (app %s, pid %d).", snapshot.WindowTitle, snapshot.App.Name, snapshot.App.PID)},
		{Type: "image", Data: snapshot.ScreenshotPNGBase64, MimeType: "image/png"},
	}}
}

func (s *service) click(app, elementIndex, declared string, x, y *float64, clickCount int, mouseButton, clickMethod string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if x != nil || y != nil {
		// 通道要能从工具名上看出来。坐标点击**不定位任何元素**——谁在那个点上
		// 谁收到——所以它是另一个工具，不是 click 的一个参数变体。
		return textResult("click no longer accepts x/y. It is the ACCESSIBILITY-channel tool and addresses targets by element_index. For a coordinate click use click_xy — it addresses by pixel, reports what the hit test found under that point, and always returns a screenshot.", true)
	}
	if elementIndex == "" {
		return textResult("click requires element_index. If the target has no index in the tree, use click_xy with window-relative pixel coordinates.", true)
	}
	if clickMethod == "accessibility" && elementIndex == "" {
		return textResult("click_method 'accessibility' requires element_index", true)
	}
	if clickMethod == "app_post" {
		return textResult("click_method 'app_post' is not supported on Linux", true)
	}
	if clickMethod == "sky_click" {
		return textResult("click_method 'sky_click' is not supported on Linux", true)
	}
	// 这里原本有一道 OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS 闸门，
	// 挡"把指针甩到屏幕上任意一点"。现在 click 必须带 element_index，
	// 裸坐标全部走 click_xy，这道闸门在这里已经不可达。
	//
	// 它防的风险由一条**更强**的保证接管：GUI 通道的坐标在运行时被
	// **夹紧在窗口矩形内**（见 runtime.py 的 screen_point）。
	// 夹紧把风险从"可能打到别的应用"降为"最多打到本窗口边缘"，
	// 而且不牺牲任何能力——GUI 是一条声明过的一等通道，
	// 不该靠环境变量才能用。
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	request := linuxRequest{
		Tool:         "click",
		App:          app,
		X:            x,
		Y:            y,
		ClickCount:   clickCount,
		MouseButton:  mouseButton,
		ClickMethod:  clickMethod,
		WindowBounds: snapshot.WindowBounds,
	}
	if elementIndex != "" {
		record, err := lookupElementFor(snapshot, elementIndex, declared, "click")
		if err != nil {
			return textResult(err.Error(), true)
		}
		if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
			return textResult(mismatch, true)
		}
		request.Element = record
	}
	return s.actionResult(app, request)
}

func (s *service) performSecondaryAction(app, elementIndex, declared, action string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if elementIndex == "" {
		return textResult("Missing required argument: element_index", true)
	}
	if action == "" {
		return textResult("Missing required argument: action", true)
	}
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	record, err := lookupElementFor(snapshot, elementIndex, declared, "invoke_element_action")
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "invoke_element_action", App: app, Element: record, Action: action, WindowBounds: snapshot.WindowBounds})
}

func (s *service) scroll(app, direction, elementIndex, declared string, pages float64) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if elementIndex == "" {
		return textResult("Missing required argument: element_index", true)
	}
	normalized := strings.ToLower(direction)
	if normalized != "up" && normalized != "down" && normalized != "left" && normalized != "right" {
		return textResult("Invalid scroll direction: "+direction, true)
	}
	if pages <= 0 {
		return textResult("pages must be > 0", true)
	}
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	record, err := lookupElementFor(snapshot, elementIndex, declared, "scroll")
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "scroll", App: app, Element: record, Direction: normalized, Pages: pages, WindowBounds: snapshot.WindowBounds})
}

// clickXY 是 GUI 通道的点击：按屏幕像素定位，树完全不参与。
//
// 与 click 分成两个工具，是为了让模型**从名字上**就知道自己在哪条通道上
// （抄的是 Playwright 的 browser_mouse_click_xy）。但光改名换不来能力，
// 所以运行时会顺带做一次命中测试，把"那个点上到底是什么"报回去——
// 坐标点击原本是个纯盲点，工具打完就走，说不出打到了什么。
func (s *service) clickXY(app string, x, y *float64, clickCount int, mouseButton string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if x == nil || y == nil {
		return textResult("click_xy requires both x and y, in window-relative pixels (the same space as the screenshot and as Frame values in the tree).", true)
	}
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	return s.actionResult(app, linuxRequest{
		Tool: "click_xy", App: app, X: x, Y: y,
		ClickCount: clickCount, MouseButton: mouseButton,
		WindowBounds: snapshot.WindowBounds,
	})
}

func (s *service) dragXY(app string, fromX, fromY, toX, toY *float64) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if fromX == nil {
		return textResult("Missing required argument: from_x", true)
	}
	if fromY == nil {
		return textResult("Missing required argument: from_y", true)
	}
	if toX == nil {
		return textResult("Missing required argument: to_x", true)
	}
	if toY == nil {
		return textResult("Missing required argument: to_y", true)
	}
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	return s.actionResult(app, linuxRequest{Tool: "drag_xy", App: app, FromX: fromX, FromY: fromY, ToX: toX, ToY: toY, WindowBounds: snapshot.WindowBounds})
}

func (s *service) typeText(app, text string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if text == "" {
		return textResult("Missing required argument: text", true)
	}
	if s.currentSnapshot(app) == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	return s.actionResult(app, linuxRequest{Tool: "type_text", App: app, Text: text})
}

func (s *service) pressKey(app, key string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if key == "" {
		return textResult("Missing required argument: key", true)
	}
	if s.currentSnapshot(app) == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	return s.actionResult(app, linuxRequest{Tool: "press_key", App: app, Key: key})
}

func (s *service) setValue(app, elementIndex, declared, value string) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if elementIndex == "" {
		return textResult("Missing required argument: element_index", true)
	}
	snapshot := s.currentSnapshot(app)
	if snapshot == nil {
		return textResult("No app state is available for "+app+". Run get_app_state before action tools.", true)
	}
	record, err := lookupElementFor(snapshot, elementIndex, declared, "set_value")
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "set_value", App: app, Element: record, Value: value, WindowBounds: snapshot.WindowBounds})
}

// deliveryWasVerified：这次动作在合成之前是否确认过目标窗口处于活动状态。
//
// 只有纯合成类工具才成立。它们走 require_window_focus，夺不到焦点就硬失败，
// 绝不把输入送去别的窗口——所以一旦执行到了这里，就说明输入确实到了这个应用。
// click 不在此列：它可能走的是语义通道，压根没经过焦点确认。
// unchangedNotes 是"动作发出去了、树却没变"时说给 agent 的话。
//
// 措辞改过一次，因为它**说过头了**。真实 agent 轨迹（Claude Code 挂 MCP 跑
// OSWorld Impress 任务）：ctrl+s 之后这条说"送达但被忽略、重复也没用"，
// 而文件**其实存下来了**——OSWorld 官方评估器判 1.0 就是证据。agent 于是多花
// 截图 + 点 Save 按钮**两步去自证**，占那次 12 步里的 17%。
//
// 成因是这条判据的结构性盲区：格式类改动与文件状态**根本不进 a11y 树**
// （同一轮还实测到右对齐生效而树字节不变）。所以"树没变"在这里推不出
// "没生效"，只能推出"树看不见"。截图现在恒带，那才是这类效果的可判之处
// ——把 agent 指过去，而不是替它下一个会错的结论。
func unchangedNotes(deliveryVerified bool, pixels pixelVerdict, attrsChanged bool) []string {
	if attrsChanged {
		// 格式属性直接报出了"什么变成了什么"，没有再讨论的余地。
		return []string{"The accessibility TREE STRUCTURE is unchanged, but the element's text attributes are not (see the [text-attrs] note above) — the action took effect. Structure and formatting are separate things in AT-SPI; only the former is rendered in the snapshot."}
	}
	// 「树没变」单独一条是**弱证据**，配上像素比对才有强弱之分。
	//
	// 这是踩出来的：ctrl+s 之后树字节不变，工具断言"送达但被忽略"，而文件其实
	// 存下来了；agent 因此多花两步自证。现在屏幕像素是**独立于树**的第二个信号，
	// 两者合起来才下结论。
	switch pixels {
	case pixelsChanged:
		return []string{"The accessibility tree is unchanged, but the SCREEN DID change (see the [pixels] note above). So the action took effect in a way the tree cannot represent — this is common for formatting, file state and canvas drawing. Do NOT treat this as a failure and do NOT repeat the input; verify what changed from the attached screenshot or from external truth such as the file on disk."}
	case pixelsIdentical:
		base := "Neither the accessibility tree nor a single pixel of the window changed. Two independent signals agree, so this is STRONG evidence the action did nothing"
		if deliveryVerified {
			return []string{base + ". Delivery itself was verified (the window was focused before synthesis), so the input arrived and was ignored: repeating it will not help. Either it is a no-op in this context, or focus sits on a different widget than you assumed."}
		}
		return []string{base + ", though delivery was not separately verified — the input may not have reached the intended widget at all."}
	}
	// 像素比不了（Gdk 不可用、窗口尺寸变了）——退回原来的措辞，不假装比过。
	if deliveryVerified {
		return []string{"This app's window was verified focused before synthesis, so the input reached this window; which widget received it is unverified. The accessibility tree, window title, focus and selection are unchanged — but that is WEAK evidence, not proof of failure: whole classes of effect never reach the tree at all. Measured on LibreOffice Impress, both applying right alignment and saving the file took effect while the tree stayed byte-identical. Judge this from the attached screenshot, or from external truth such as the file on disk, BEFORE concluding it did nothing."}
	}
	return []string{"Nothing observable changed in the accessibility tree: window title, tree, focus and selection are identical to the state before this action. That is weak evidence rather than proof — effects that live outside the tree (formatting, file state, canvas pixels) leave it byte-identical. Check the attached screenshot before treating this as a failure."}
}

type pixelVerdict int

const (
	pixelsUnknown pixelVerdict = iota
	pixelsIdentical
	pixelsChanged
)

// readTextAttributeChange 看运行时有没有报出格式属性的变化。
//
// 这是**最强**的一档证据：它直接说出"justification 从 left 变成 right"，
// 而像素只能说"0.1% 变了"。有它在就不必再谈"树没变所以可能失败"。
// textAttributeChanges 比对两份快照里同一元素的格式属性。
//
// 这条通道是查 Playwright 时反推出来的：它的 aria snapshot 只渲染一小撮语义属性
// （[checked] [level=1]），"查任意样式属性"是 toHaveCSS 这类**断言**的事——
// **快照是给 agent 看的摘要，断言是精确查询，两者不必是同一套字段。**
//
// 顺着这条思路回头查 AT-SPI，才发现 Atspi.Text.get_default_attributes() 一直
// 能读到 justification / size / weight / fg-color。此前记进文档的
// "改段落对齐后 a11y 树字节不变"**是错的**——树不变是因为我们没取这些字段，
// 不是信息不存在。整场"像素噪声和光标闪烁同量级"的仗，本可以绕过去。
//
// 比对不花额外的 AT-SPI 调用：属性跟着 record 走，而动作前的快照 Go 侧本来
// 就缓存着。按 element 身份配对（automationId 优先，其次 role+name），
// 不按下标——下标每次快照都会重排。
func textAttributeChanges(before, after *appSnapshot) []string {
	if before == nil || after == nil {
		return nil
	}
	// 按 **runtimeId（树里的路径）** 配对，不按 role+name。
	//
	// 第一版用 role+name，实测当场翻车：LibreOffice 侧栏有四个无名 `text` 节点，
	// key 全是 `text\x00""` 撞成一个，于是某个节点的悬停高亮（bg-color）
	// 被张冠李戴到别人头上，连空操作都报出"格式变了"。
	// 路径在同一份 UI 的两次快照之间是稳定且唯一的。
	key := func(r elementRecord) string {
		if len(r.RuntimeID) == 0 {
			return "idx:" + strconv.Itoa(r.Index)
		}
		parts := make([]string, 0, len(r.RuntimeID))
		for _, n := range r.RuntimeID {
			parts = append(parts, strconv.Itoa(n))
		}
		return "path:" + strings.Join(parts, ".")
	}
	old := map[string]map[string]string{}
	for _, record := range before.Elements {
		if len(record.TextAttributes) > 0 {
			old[key(record)] = record.TextAttributes
		}
	}
	var changes []string
	seen := map[string]bool{}
	for _, record := range after.Elements {
		if len(record.TextAttributes) == 0 {
			continue
		}
		k := key(record)
		previous, ok := old[k]
		if !ok || seen[k] {
			continue
		}
		seen[k] = true
		var diffs []string
		for name, value := range record.TextAttributes {
			if was, had := previous[name]; !had || was != value {
				from := was
				if !had {
					from = "unset"
				}
				diffs = append(diffs, fmt.Sprintf("%s: %s -> %s", name, from, value))
			}
		}
		if len(diffs) == 0 {
			continue
		}
		sort.Strings(diffs)
		label := describeRecord(&record)
		changes = append(changes, fmt.Sprintf("%s — %s", label, strings.Join(diffs, "; ")))
	}
	sort.Strings(changes)
	return changes
}

func readTextAttributeChange(notes []string) bool {
	for _, note := range notes {
		if strings.HasPrefix(note, "[text-attrs]") {
			return true
		}
	}
	return false
}

// readPixelVerdict 从运行时的 `[pixels]` Note 里读出屏幕到底变没变。
func readPixelVerdict(notes []string) pixelVerdict {
	for _, note := range notes {
		if !strings.HasPrefix(note, "[pixels]") {
			continue
		}
		if strings.Contains(note, "pixel-identical") {
			return pixelsIdentical
		}

		return pixelsChanged
	}
	return pixelsUnknown
}

func deliveryWasVerified(request linuxRequest) bool {
	switch request.Tool {
	case "press_key", "scroll", "drag_xy", "click_xy":
		return true
	}
	return false
}

// usedSemanticPath 判断这次动作实际走的是不是语义调用。
// 运行时给每条 Note 打了通道标签，这里只认标签，不猜。
func usedSemanticPath(notes []string) bool {
	for _, note := range notes {
		if strings.Contains(note, "[semantic]") {
			return true
		}
	}
	return false
}

// shouldRetryWithSynthesis：语义调用报了成功却什么都没发生时，该不该改走坐标合成。
//
// 只对 click_method "auto" 开放。想"只走语义、绝不合成"的调用方用
// "accessibility"，想"只合成"的用 "global"——两个显式选项本来就在 schema 里，
// 不需要为这件事新增参数。
//
// 必须带 element_index：落点由无障碍树给出，不是屏幕上任意一点。
func shouldRetryWithSynthesis(request linuxRequest, notes []string) bool {
	return request.Tool == "click" &&
		strings.EqualFold(request.ClickMethod, "auto") &&
		request.Element != nil &&
		usedSemanticPath(notes)
}

// tracePath 每个动作写一行 JSONL 的目标文件。**默认关闭**。
//
// Playwright 的 trace viewer 是它最能打的运维特性：出了问题不用复现，
// 直接看当时每一步的前后状态。我们此前出问题只能回读 transcript，
// 而 transcript 里没有动作前后的完整状态。
//
// 默认关闭，因为它每步都要序列化两份树摘要，而绝大多数调用不需要事后复盘。
// 打开的方式是给出路径，而不是布尔开关：写到哪里是调用方的事，
// 一个诊断设施不该自作主张地选目录。
func tracePath() string {
	return strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_TRACE_FILE"))
}

// traceSummary 一份树的**摘要**，不是整棵树。
//
// 整棵树写进 trace 会让文件比 transcript 还大（VS Code 一棵树 15342 字符，
// 一次基线跑 17 个动作），而复盘时真正要看的是"前后有没有变、变了哪里"。
// 要看细节有 error-context 那条路。
func traceSummary(snapshot *appSnapshot) map[string]any {
	if snapshot == nil {
		return nil
	}
	return map[string]any{
		"window":   snapshot.WindowTitle,
		"elements": len(snapshot.Elements),
		"focused":  snapshot.FocusedSummary,
		"selected": snapshot.SelectedText,
	}
}

func (s *service) appendTrace(app string, request linuxRequest, before, after *appSnapshot, notes []string, failed bool) {
	path := tracePath()
	if path == "" {
		return
	}
	record := map[string]any{
		"at":     time.Now().Format(time.RFC3339Nano),
		"tool":   request.Tool,
		"app":    app,
		"failed": failed,
		"before": traceSummary(before),
		"after":  traceSummary(after),
		"notes":  notes,
	}
	// 动作参数照抄请求，但**不含快照**：request 里带着 Element 记录和
	// windowBounds，原样写进去会把每行撑到几 KB。
	args := map[string]any{}
	if request.Element != nil {
		args["element_index"] = request.Element.Index
		args["element_role"] = request.Element.ControlType
		args["element_name"] = request.Element.Name
	}
	for key, value := range map[string]any{
		"x": request.X, "y": request.Y, "from_x": request.FromX, "from_y": request.FromY,
		"to_x": request.ToX, "to_y": request.ToY,
	} {
		if pointer, ok := value.(*float64); ok && pointer != nil {
			args[key] = *pointer
		}
	}
	for key, value := range map[string]string{
		"action": request.Action, "direction": request.Direction,
		"text": request.Text, "key": request.Key, "value": request.Value,
	} {
		if value != "" {
			args[key] = value
		}
	}
	record["args"] = args

	line, err := json.Marshal(record)
	if err != nil {
		return
	}
	// 追加写，失败**静默放弃**：trace 是诊断设施，不该因为磁盘满就让一次
	// 正常的动作报错。这条纪律和 error-context 是同一条。
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if os.MkdirAll(dir, 0o755) != nil {
			return
		}
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer file.Close()
	file.Write(append(line, '\n'))
}

func (s *service) actionResult(app string, request linuxRequest) toolCallResult {
	// 动作前的状态在 Go 侧已经缓存着了（动作工具契约要求先调 get_app_state，
	// 且每个动作自己也会刷新缓存），所以 before/after 比对不需要额外遍历一次树。
	before := s.currentSnapshot(app)
	snapshot, notes, result := s.refreshSnapshot(app, request)
	if result.IsError {
		// 失败也要留一行。只记成功的 trace 会让复盘时看到一串顺利的动作，
		// 而真正要查的那一步凭空消失。
		s.appendTrace(app, request, before, nil, nil, true)
		return result
	}
	// 格式属性的变化排在最前面：它说得出**什么变成了什么**，
	// 而像素只说得出**有事发生**、树只说得出**结构没变**。
	if changes := textAttributeChanges(before, snapshot); len(changes) > 0 {
		shown := changes
		if len(shown) > 4 {
			shown = append(append([]string{}, shown[:4]...),
				fmt.Sprintf("… and %d more", len(changes)-4))
		}
		notes = append([]string{"[text-attrs] Formatting changed, read straight from the AT-SPI text attributes: " +
			strings.Join(shown, " | ") +
			". This is direct evidence the action took effect — the snapshot renders structure, not formatting, so the tree can look unchanged while this does not."}, notes...)
	}
	if before != nil && !observablyChanged(before, snapshot) {
		// 语义调用返回成功却什么都没发生——实测这不是罕见情况，Nautilus 文件
		// 图标的 `menu`、GIMP 图层 cell 的 `activate`、VLC 单选按钮的 `Toggle`
		// 都如此。此时 do_action 返回 True，
		// `auto` 的原有回落分支（只在返回 False 时触发）不会启动，
		// agent 手里没有任何一条能走通的路。
		//
		// 判据刻意只用**外部信号**：窗口标题、整棵树、焦点、选中。
		// 不读被操作节点自身的状态——VLC 那颗单选按钮 Toggle 之后 CHECKED
		// 真的翻转了、面板却不切换，读节点自身会把它判成"生效了"。
		//
		// **这个机制盖不住 VLC 那一类，要说清楚**：它只认"什么都没变"。
		// VLC 的案例是"状态变了、行为没变"——CHECKED 翻转会让树跟着变，
		// 于是这里判定"发生了变化"，不会重试。要接住那一类，需要知道
		// 这次动作**本该**造成什么后果（比如面板应当切换），
		// 那是任务级的语义，不是通用判据能给的。
		// 所以这里覆盖的是静默无操作，不是全部执行失败。
		//
		// 重复执行的风险是可控的：只有整棵树逐行相同、标题/焦点/选中都没变时
		// 才重试，也就是应用状态与动作前完全一致，此时再点一次等价于从同一
		// 状态点第一次。会漏判的只有"生效了但界面毫无痕迹"这一类，
		// 而当前的失败模式——静默无操作——是实测在 3 个应用上都会发生的。
		if shouldRetryWithSynthesis(request, notes) {
			retry := request
			retry.ClickMethod = "global"
			retrySnapshot, retryNotes, retryResult := s.refreshSnapshot(app, retry)
			if !retryResult.IsError && retrySnapshot != nil {
				notes = append(notes,
					"The semantic action reported success but nothing observably changed, so this retried the same element as a coordinate click.")
				notes = append(notes, retryNotes...)
				snapshot = retrySnapshot
				if !observablyChanged(before, snapshot) {
					notes = append(notes, "Still nothing observably changed after the coordinate retry. Both execution paths are exhausted for this element — do not assume the click landed.")
				}
			} else {
				notes = append(notes,
					"The semantic action reported success but nothing observably changed, and the coordinate retry could not run. Treat the action as unconfirmed.")
			}
		} else if deliveryWasVerified(request) {
			// 键盘/滚动/拖拽只有合成这一条通道，没有第二条可回落，所以对它们
			// 有价值的不是重试，而是**把"送达"和"生效"分开讲**。
			//
			// 这两件事在这里是能分开的：合成之前 require_window_focus 已经确认
			// 目标窗口处于活动状态，否则会直接硬失败而不是把输入送去别处。
			// 因此"什么都没变"在这里的含义是**应用收到了但没有反应**，
			// 而不是 click 那种"动作可能压根没送到"。
			//
			// 这个区别直接决定 agent 下一步该干什么：同一个按键再按一次不会有
			// 不同结果，该换路子；而不是像 click 那样值得换通道重试。
			// 措辞这里改过一次，因为它**说过头了**。
			//
			// 真实 agent 轨迹（Claude Code 跑 OSWorld Impress 任务）：ctrl+s
			// 之后这条 Note 说"送达但被忽略、重复也没用"，而文件**其实存下来了**
			// ——OSWorld 官方评估器判 1.0 就是证据。agent 于是多花了截图 + 点
			// Save 按钮**两步去自证**，占那次 12 步里的 17%。
			//
			// 成因是这条判据的结构性盲区：格式类改动与文件状态**根本不进 a11y
			// 树**（同一轮还实测到右对齐生效而树字节不变）。所以"树没变"在这里
			// 不能推出"没生效"，只能推出"树看不见"。
			// 截图现在恒带，那才是这类效果的可判之处——把 agent 指过去，
			// 而不是替它下一个会错的结论。
			notes = append(notes, unchangedNotes(true, readPixelVerdict(notes), readTextAttributeChange(notes))...)
		} else {
			notes = append(notes, unchangedNotes(false, readPixelVerdict(notes), readTextAttributeChange(notes))...)
		}
	}
	// 写在最后：此时 notes 已经攒齐（像素判据、状态回读、解析目标全在里面），
	// 而那些 Note 正是复盘时最想看的东西。
	s.appendTrace(app, request, before, snapshot, notes, false)
	if diff, ok := incrementalTree(before, snapshot); ok {
		return snapshot.resultWithTreeOverride(notes, diff)
	}
	return snapshot.resultWithNotes(notes)
}

// incrementalTree 在安全且划算时，用"只给变化的行"替代整棵树。
//
// 实测（gedit 轨迹）：无结构变化的步骤上增量能省 62%，其中若干步的 diff 直接是 0；
// 但有结构变化的步骤上增量反而**亏 7%**——增删两边都要付钱，加起来超过全量。
// 所以不能无条件用增量。
//
// 判据是行数不变。这一条同时解决了两个问题：
//  1. 成本：行数不变意味着只有内容变了，diff 必然小于全量
//  2. 正确性：#15 实测表明结构一变 element_index 就永久重排（gedit 上 26%），
//     而行数不变正是"没有结构变化"的充分信号，此时索引 0% 漂移，
//     agent 可以安全沿用上一轮观测里的索引
//
// 两个条件恰好对齐，不需要额外的稳定标识。
func incrementalTree(before, after *appSnapshot) ([]string, bool) {
	if before == nil || after == nil {
		return nil, false
	}
	if len(before.TreeLines) != len(after.TreeLines) || len(after.TreeLines) == 0 {
		return nil, false
	}
	changed := make([]string, 0, 8)
	for i := range after.TreeLines {
		if before.TreeLines[i] != after.TreeLines[i] {
			changed = append(changed, after.TreeLines[i])
		}
	}
	if len(changed) == 0 || len(changed)*3 >= len(after.TreeLines) {
		// 变化超过三分之一就没必要绕弯，直接给全量更好读。
		return nil, false
	}
	header := fmt.Sprintf(
		"Incremental view: %d of %d tree lines changed since your previous get_app_state; "+
			"everything else is byte-identical and keeps the same element_index. "+
			"Call get_app_state again for the full tree.",
		len(changed), len(after.TreeLines))
	return append([]string{header}, changed...), true
}

// observablyChanged 比较动作前后两份快照里 agent 能观察到的部分。
// 截图不参与比较：光标闪烁之类的像素噪音会让它永远"有变化"。
func observablyChanged(before, after *appSnapshot) bool {
	if before == nil || after == nil {
		return true
	}
	if before.WindowTitle != after.WindowTitle ||
		before.FocusedSummary != after.FocusedSummary ||
		before.SelectedText != after.SelectedText {
		return true
	}
	if len(before.TreeLines) != len(after.TreeLines) {
		return true
	}
	for i := range before.TreeLines {
		if before.TreeLines[i] != after.TreeLines[i] {
			return true
		}
	}
	return false
}

func (s *service) currentSnapshot(app string) *appSnapshot {
	return s.snapshots[strings.ToLower(app)]
}

func (s *service) refreshSnapshot(app string, request linuxRequest) (*appSnapshot, []string, toolCallResult) {
	if previous := s.currentSnapshot(app); previous != nil && len(previous.Refs) > 0 {
		request.KnownRefs = previous.Refs
	}
	response, err := runPython(request)
	if err != nil {
		return nil, nil, textResult(err.Error(), true)
	}
	if !response.OK {
		return nil, nil, textResult(response.Error, true)
	}
	if response.Snapshot == nil {
		return nil, nil, textResult("Linux runtime did not return an app snapshot.", true)
	}
	s.rememberSnapshot(app, response.Snapshot)
	return response.Snapshot, response.Notes, toolCallResult{}
}

func (s *service) rememberSnapshot(query string, snapshot *appSnapshot) {
	snapshot.capturedAt = time.Now()
	snapshot.sourceApp = query
	keys := []string{query, snapshot.App.Name, snapshot.App.BundleIdentifier, strconv.Itoa(snapshot.App.PID)}
	for _, key := range keys {
		key = strings.ToLower(strings.TrimSpace(key))
		if key != "" {
			s.snapshots[key] = snapshot
		}
	}
}

// toolError 按 Playwright 的错误形态组装：**事实链在前，建议在后**。
//
// Playwright 的动作超时错误长这样（1.55.1，issue #37695）：
//
//	Error: locator.click: Test timeout of 30000ms exceeded.
//	Call log:
//	  - waiting for getByText('Get started')
//	    - locator resolved to <a href="/docs/intro">Get started</a>
//	  - attempting click action
//	    - waiting for element to be visible, enabled and stable
//
// 它给的不是建议，是**事实链**：我在等什么、解析到了什么、检查到哪一步、
// 失败在哪个状态。我全量查过它的源码，连 "Consider using force: true" 这种
// 建议串都不存在。
//
// **唯一的例外恰好在它的 MCP 层**——因为消费者是 LLM 不是人：
//
//	Ref e3 not found in the current page snapshot. Try capturing new snapshot.
//
// 这条**点名了下一步该调哪个工具**。我们的消费者同样是 LLM，所以两者都要：
// 事实链 + 明确的下一步，但**分开标注**，别把推测混进 call log。
//
// 替换掉的是 `unknown element_index "128"` 这种 27 个字符、零指引的报错。
type toolError struct {
	tool    string
	summary string
	fields  [][2]string
	log     []string
	next    string
}

func (e *toolError) add(name, value string) *toolError {
	if value != "" {
		e.fields = append(e.fields, [2]string{name, value})
	}
	return e
}

func (e *toolError) step(format string, args ...any) *toolError {
	e.log = append(e.log, fmt.Sprintf(format, args...))
	return e
}

func (e *toolError) Error() string {
	var b strings.Builder
	if e.tool != "" {
		b.WriteString(e.tool + ": ")
	}
	b.WriteString(e.summary)
	if len(e.fields) > 0 {
		width := 0
		for _, f := range e.fields {
			if len(f[0]) > width {
				width = len(f[0])
			}
		}
		b.WriteString("\n")
		for _, f := range e.fields {
			b.WriteString(fmt.Sprintf("\n%-*s %s", width+1, f[0]+":", f[1]))
		}
	}
	if len(e.log) > 0 {
		b.WriteString("\n\nCall log:")
		for _, line := range e.log {
			// 已经带缩进的是上一条的续行（候选列表那种），不再套 "- "。
			// 照 Playwright 的 call log 缩进规则：带前导空格的渲染成子行。
			if strings.HasPrefix(line, " ") {
				b.WriteString("\n  " + line)
			} else {
				b.WriteString("\n  - " + line)
			}
		}
	}
	if e.next != "" {
		b.WriteString("\n\nNext: " + e.next)
	}
	return b.String()
}

// snapshotAge 把"这份快照有多旧"写成一句话。
func snapshotAge(snapshot *appSnapshot) string {
	if snapshot == nil {
		return ""
	}
	indices := "no elements"
	if n := len(snapshot.Elements); n > 0 {
		indices = fmt.Sprintf("%d elements (indices %d..%d)", n,
			snapshot.Elements[0].Index, snapshot.Elements[n-1].Index)
	}
	if snapshot.capturedAt.IsZero() {
		return indices
	}
	return fmt.Sprintf("captured %.1fs ago, %s",
		time.Since(snapshot.capturedAt).Seconds(), indices)
}

// closestElements 找出与声明意图最像的几个元素，连同可直接使用的选择器一起给出。
// 对应 Playwright strict mode 违规时那份 "aka <可用的替代 locator>" 候选表。
func closestElements(snapshot *appSnapshot, declared string, limit int) []string {
	if snapshot == nil || strings.TrimSpace(declared) == "" {
		return nil
	}
	var tokens []string
	for _, token := range strings.FieldsFunc(strings.ToLower(declared), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		if len([]rune(token)) >= 2 && !genericIntentWords[token] {
			tokens = append(tokens, token)
		}
	}
	if len(tokens) == 0 {
		return nil
	}
	var out []string
	for _, record := range snapshot.Elements {
		haystack := strings.ToLower(record.Name + " " + record.Description)
		hit := false
		for _, token := range tokens {
			if strings.Contains(haystack, token) {
				hit = true
				break
			}
		}
		if !hit {
			continue
		}
		line := fmt.Sprintf("    %-4d %s", record.Index, describeRecord(&record))
		if record.Name != "" {
			line = fmt.Sprintf("    %-4d %-26s aka %s %q",
				record.Index, describeRecord(&record), record.ControlType, record.Name)
		} else if record.Description != "" {
			line = fmt.Sprintf("    %-4d %-26s aka %s %q",
				record.Index, describeRecord(&record), record.ControlType, record.Description)
		}
		out = append(out, line)
		if len(out) >= limit {
			break
		}
	}
	return out
}

func lookupElement(snapshot *appSnapshot, elementIndex string) (*elementRecord, error) {
	return lookupElementFor(snapshot, elementIndex, "", "")
}

// lookupElementFor 解析一个引用，失败时给出**完整的事实链**而不是一句话。
func lookupElementFor(snapshot *appSnapshot, elementIndex, declared, tool string) (*elementRecord, error) {
	fail := func(summary string) *toolError {
		e := &toolError{tool: tool, summary: summary}
		e.add("Requested", strconv.Quote(elementIndex))
		e.add("Declared", func() string {
			if declared == "" {
				return ""
			}
			return strconv.Quote(declared)
		}())
		e.add("Snapshot", snapshotAge(snapshot))
		return e
	}

	index, err := strconv.Atoi(elementIndex)
	if err != nil {
		return lookupBySelectorFor(snapshot, elementIndex, declared, tool)
	}
	for _, record := range snapshot.Elements {
		if record.Index == index {
			copy := record
			return &copy, nil
		}
	}
	e := fail(fmt.Sprintf("element_index %d is not in the current snapshot.", index)).
		step("resolving element_index=%d against the snapshot of %q", index, snapshot.sourceApp).
		step("that snapshot has no such index")
	if candidates := closestElements(snapshot, declared, 5); len(candidates) > 0 {
		e.step("elements matching what you said you were targeting:")
		e.log = append(e.log, candidates...)
	}
	e.next = "call get_app_state again and read the index from the fresh tree. Indices are renumbered whenever the UI changes, so an index from an earlier snapshot is not reusable. A selector such as `push button \"Save\"` survives renumbering and can be passed here instead."
	return nil, e
}

func elementIntentMismatch(record *elementRecord, declared string) string {
	declared = strings.TrimSpace(declared)
	if declared == "" || record == nil {
		return ""
	}
	// 只对**有名字**的元素校验。
	//
	// 第一版把 role 也算进可匹配范围，结果被最常见的词击穿：声明里几乎必然有
	// "button"，而 role 里也几乎必然有 "button"，于是"声明 Save 却给了 New 的
	// 下标"这种正要拦的情况照样放行（实测确认）。
	//
	// 反过来，无名元素的身份常常来自**旁边的标签**而不是自身属性——
	// 实测 LibreOffice 的「位置和大小」对话框，四个 spin button 全都没有名字，
	// "哪个是 Position Y" 只写在旁边的 label 上。这类元素上任何声明都匹配不了，
	// 硬校验就是纯误伤。**判不了就别判。**
	if strings.TrimSpace(record.Name) == "" {
		return ""
	}
	if !containsASCIILetter(declared) {
		// 声明是非 ASCII 文字（例如中文），而 name 通常是英文。判不了。
		return ""
	}
	haystack := strings.ToLower(record.Name + " " + record.Description)
	matched := false
	specific := 0
	for _, token := range strings.FieldsFunc(strings.ToLower(declared), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		if len([]rune(token)) < 2 || genericIntentWords[token] {
			continue
		}
		specific++
		if strings.Contains(haystack, token) {
			matched = true
			break
		}
	}
	if specific == 0 || matched {
		// 声明里没有区分性的实词（"the button"），或者对上了。
		return ""
	}
	return fmt.Sprintf(
		"element_index %d resolves to %s, which does not match what you said you were targeting (%q). "+
			"This usually means the index came from an earlier snapshot — indices are renumbered whenever a dialog opens or closes. "+
			"Call get_app_state again and re-read the index. If you really did mean this element, repeat the call with an `element` description that names it.",
		record.Index, describeRecord(record), declared)
}

// 对任何元素都成立的词。拿它们判等于不判——而且第一版正是被 "button" 击穿的。
var genericIntentWords = map[string]bool{
	"the": true, "a": true, "an": true, "of": true, "in": true, "on": true,
	"for": true, "to": true, "this": true, "that": true, "and": true, "with": true,
	"button": true, "item": true, "box": true, "field": true, "menu": true,
	"list": true, "cell": true, "pane": true, "panel": true, "window": true,
	"dialog": true, "label": true, "icon": true, "tab": true, "bar": true,
	"view": true, "control": true, "element": true, "text": true, "entry": true,
	"checkbox": true, "check": true, "radio": true, "toggle": true, "spin": true,
	"push": true, "click": true, "select": true, "option": true,
}

func containsASCIILetter(text string) bool {
	for _, r := range text {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			return true
		}
	}
	return false
}

func describeRecord(record *elementRecord) string {
	if record.Name != "" {
		return fmt.Sprintf("%s %q", record.ControlType, record.Name)
	}
	return fmt.Sprintf("an unnamed %s", record.ControlType)
}

// ---------------------------------------------------------------------------
// find：不用先把整棵树拉下来就能定位
//
// 这是我们与 Playwright 之间**身份级**的差距，不是体验问题。Playwright 的
// locator（`getByRole('button', {name:'OK'})`）从不要求你先 dump 一份 DOM；
// 我们此前只有"整棵拉下来再挑一个"这一条路——VS Code 的树 15342 字符，
// 为了点一个按钮让 agent 把这些读完，是纯粹的浪费。
//
// **必须说清它不省什么**：运行时那边照样遍历一整棵 AT-SPI 树，机器成本一分没少。
// 省的是 agent 的上下文。桌面上不存在 CSS 选择器那种能下推给引擎的查询，
// 遍历避不掉——这一点写进工具描述，免得调用方以为 find 比 get_app_state "快"。
// ---------------------------------------------------------------------------

// linesByIndex 把渲染好的树行按行首编号建索引。
//
// 刻意**复用 Python 渲染的原文**，而不在 Go 里再写一个渲染器：文法只有一份，
// 两份实现迟早会漂移，而 find 的输出和 get_app_state 的输出必须逐字同构——
// 否则 agent 得学两套读法。截断提示行（"… 12 more"）行首不是数字，自然被跳过。
func linesByIndex(snapshot *appSnapshot) map[int]string {
	out := map[int]string{}
	for _, line := range snapshot.TreeLines {
		trimmed := strings.TrimLeft(line, " \t")
		head := trimmed
		if space := strings.IndexByte(trimmed, ' '); space > 0 {
			head = trimmed[:space]
		}
		index, err := strconv.Atoi(head)
		if err != nil {
			continue
		}
		if _, seen := out[index]; !seen {
			out[index] = line
		}
	}
	return out
}

func containsFold(haystack, needle string) bool {
	return strings.Contains(strings.ToLower(haystack), strings.ToLower(needle))
}

// recordMatches 判一条记录是否满足查询。多个条件是**与**关系。
func recordMatches(record *elementRecord, role, name, text, state string) bool {
	if role != "" &&
		!containsFold(record.ControlType, role) &&
		!containsFold(record.LocalizedControlType, role) {
		return false
	}
	if name != "" && !containsFold(record.Name, name) {
		return false
	}
	if state != "" && !containsFold(record.States, state) {
		return false
	}
	if text != "" {
		// text 是"我不知道这串字出现在哪个字段"时的兜底：名字、描述、值、
		// 占位符都扫。桌面上同一句话落在哪个字段**极不稳定**——实测同一个
		// 搜索框在 GTK 里是 placeholder、在 Electron 里是 name。
		if !containsFold(record.Name, text) &&
			!containsFold(record.Description, text) &&
			!containsFold(record.Value, text) &&
			!containsFold(record.Placeholder, text) {
			return false
		}
	}
	return true
}

func describeQuery(role, name, text, state string) string {
	parts := []string{}
	for _, pair := range [][2]string{
		{"role", role}, {"name", name}, {"text", text}, {"state", state},
	} {
		if pair[1] != "" {
			parts = append(parts, fmt.Sprintf("%s~%q", pair[0], pair[1]))
		}
	}
	return strings.Join(parts, " ")
}

func (s *service) find(app, role, name, text, state string, limit int) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if role == "" && name == "" && text == "" && state == "" {
		return textResult("find needs at least one of role, name, text, or state. To see the whole tree, call get_app_state.", true)
	}
	if limit <= 0 {
		limit = 20
	}
	noScreenshot := false
	snapshot, notes, result := s.refreshSnapshot(app, linuxRequest{
		Tool:              "get_app_state",
		App:               app,
		IncludeScreenshot: &noScreenshot,
	})
	if result.IsError {
		return result
	}

	lines := linesByIndex(snapshot)
	matched := []elementRecord{}
	for _, record := range snapshot.Elements {
		if recordMatches(&record, role, name, text, state) {
			matched = append(matched, record)
		}
	}

	query := describeQuery(role, name, text, state)
	var out strings.Builder
	if len(matched) == 0 {
		// 零命中**不是错误**，是一个如实的答案。Playwright 在动作时找不到元素才
		// 报错，查询本身返回空集是正常的。这里给的是收敛线索，不是失败。
		fmt.Fprintf(&out, "No element in %q matches %s.\n", snapshot.WindowTitle, query)
		fmt.Fprintf(&out, "Searched %d elements. ", len(snapshot.Elements))
		out.WriteString("All filters are ANDed and matched as case-insensitive substrings — drop one, or call get_app_state to see what is actually there.")
		hintFrom := name
		if hintFrom == "" {
			hintFrom = text
		}
		if near := closestElements(snapshot, hintFrom, 5); len(near) > 0 {
			out.WriteString("\nClosest by name:\n")
			for _, line := range near {
				out.WriteString("  " + line + "\n")
			}
		}
		return snapshot.textWithNotes(out.String(), notes)
	}

	shown := matched
	if len(shown) > limit {
		shown = shown[:limit]
	}
	fmt.Fprintf(&out, "%d element(s) in %q match %s", len(matched), snapshot.WindowTitle, query)
	if len(shown) < len(matched) {
		fmt.Fprintf(&out, " — showing the first %d, raise `limit` for the rest", len(shown))
	}
	out.WriteString(".\nLines are verbatim from the tree, so element_index below is directly usable by click / set_value / invoke_element_action.\n\n")
	for _, record := range shown {
		if line, ok := lines[record.Index]; ok {
			// 去掉缩进：命中集是**平的**，保留缩进会暗示一个并不存在的父子关系。
			out.WriteString(strings.TrimLeft(line, " \t") + "\n")
		} else {
			fmt.Fprintf(&out, "%d %s\n", record.Index, describeRecord(&record))
		}
	}
	return snapshot.textWithNotes(out.String(), notes)
}

// ---------------------------------------------------------------------------
// verify：带自动重试的断言
//
// 此前 `grep -c '"verify_'` = 0——一个断言工具都没有。动作后的状态回读是
// **被动**的：它只告诉你"这次动作之后树里变了什么"。agent 想主动确认一件事
// （"保存对话框关掉了没有"）只能再拉一棵树自己比对，而那是**一次性**判定，
// 早一拍就得到错的答案。
//
// Playwright 的 `expect(...).toBeVisible()` 之所以可靠，关键不在断言本身，
// 在于它**在超时窗口内反复重查**。桌面上这一点只会更重要：UI 变化没有
// load 事件，settle 等待有实测过的边界（0.12s 之后才出现的窗口抓不到）。
// 轮询正是那条边界的补救。
//
// 代价要如实说：每一轮都是一次完整的 AT-SPI 遍历，不便宜。所以默认超时压在
// 5 秒、轮询间隔 400ms，且**第一轮一定会执行**——GIMP 单次快照实测就要 30s，
// 若按"先判超时再取样"写，那种应用上 verify 会一次都不采样就报失败。
// ---------------------------------------------------------------------------

type verifyGoal struct {
	state         string
	valueContains string
	textContains  string
	exists        *bool
}

func (g verifyGoal) empty() bool {
	return g.state == "" && g.valueContains == "" && g.textContains == "" && g.exists == nil
}

func (g verifyGoal) describe() string {
	parts := []string{}
	if g.exists != nil {
		if *g.exists {
			parts = append(parts, "to exist")
		} else {
			parts = append(parts, "to be gone")
		}
	}
	if g.state != "" {
		if bare, negated := strings.CutPrefix(g.state, "!"); negated {
			parts = append(parts, fmt.Sprintf("not to be %s", bare))
		} else {
			parts = append(parts, fmt.Sprintf("to be %s", g.state))
		}
	}
	if g.valueContains != "" {
		parts = append(parts, fmt.Sprintf("value to contain %q", g.valueContains))
	}
	if g.textContains != "" {
		parts = append(parts, fmt.Sprintf("text to contain %q", g.textContains))
	}
	return strings.Join(parts, " and ")
}

// check 返回是否满足，以及**实际观测到了什么**。第二个返回值是这个工具的重点：
// "断言失败"本身没有信息量，"期望 checked，实际 enabled focused"才有。
func (g verifyGoal) check(record *elementRecord, lookupErr error) (bool, string) {
	if record == nil {
		if g.exists != nil && !*g.exists {
			return true, "no element matches the selector"
		}
		reason := "no element matches the selector"
		if lookupErr != nil {
			reason = firstLine(lookupErr.Error())
		}
		return false, reason
	}
	observed := []string{fmt.Sprintf("resolved to %d %s", record.Index, describeRecord(record))}
	ok := true
	if g.exists != nil && !*g.exists {
		ok = false
		observed = append(observed, "but it is still present")
	}
	if g.state != "" {
		want := true
		bare := g.state
		if trimmed, negated := strings.CutPrefix(g.state, "!"); negated {
			want, bare = false, trimmed
		}
		has := containsFold(record.States, bare)
		if has != want {
			ok = false
		}
		// States 渲染出来时**自带方括号**（树里就是 `[enabled focused]`），
		// 这里再包一层会变成 `[ [enabled focused]]`。照抄树的写法，不加工。
		shown := strings.TrimSpace(record.States)
		if shown == "" {
			shown = "(no states reported)"
		}
		observed = append(observed, "states are "+shown)
	}
	if g.valueContains != "" {
		if !containsFold(record.Value, g.valueContains) {
			ok = false
		}
		observed = append(observed, fmt.Sprintf("value is %q", record.Value))
	}
	if g.textContains != "" {
		hit := containsFold(record.Name, g.textContains) ||
			containsFold(record.Description, g.textContains) ||
			containsFold(record.Value, g.textContains) ||
			containsFold(record.Placeholder, g.textContains)
		if !hit {
			ok = false
		}
		observed = append(observed, fmt.Sprintf("name=%q value=%q", record.Name, record.Value))
	}
	return ok, strings.Join(observed, ", ")
}

func firstLine(text string) string {
	if index := strings.IndexByte(text, '\n'); index >= 0 {
		return text[:index]
	}
	return text
}

func (s *service) verify(app, selector string, goal verifyGoal, timeoutMS int) toolCallResult {
	if app == "" {
		return textResult("Missing required argument: app", true)
	}
	if strings.TrimSpace(selector) == "" {
		return textResult("verify requires element_index — a selector like `push button \"Save\"` or an index from the last get_app_state.", true)
	}
	if goal.empty() {
		return textResult("verify needs something to assert: pass at least one of state, value_contains, text_contains, or exists.", true)
	}
	if timeoutMS <= 0 {
		timeoutMS = 5000
	}

	noScreenshot := false
	deadline := time.Now().Add(time.Duration(timeoutMS) * time.Millisecond)
	attempts := []string{}
	for attempt := 1; ; attempt++ {
		snapshot, _, result := s.refreshSnapshot(app, linuxRequest{
			Tool:              "get_app_state",
			App:               app,
			IncludeScreenshot: &noScreenshot,
		})
		if result.IsError {
			return result
		}
		// 必须走 lookupElementFor 而不是 lookupBySelectorFor：前者数字下标和
		// 选择器都认，后者只认选择器。实测代价——传 "62" 时它会连报 6 轮
		// `no element matches the selector "62"`，看上去像元素真的不见了，
		// 而元素一直在那儿。一个断言工具给出这种假阴性比没有断言更糟。
		record, lookupErr := lookupElementFor(snapshot, selector, "", "verify")
		ok, observed := goal.check(record, lookupErr)
		attempts = append(attempts, fmt.Sprintf("attempt %d at +%dms — %s",
			attempt, timeoutMS-int(time.Until(deadline).Milliseconds()), observed))
		if ok {
			return textResult(fmt.Sprintf(
				"PASS: %s %s.\n%s\nAssertions do not change the UI; this is a read of the live tree, not a replay of an earlier snapshot.",
				selector, goal.describe(), observed), false)
		}
		// 先取样再判超时：见上面 GIMP 的理由，第一轮无论如何都要跑完。
		if !time.Now().Before(deadline) {
			e := &toolError{tool: "verify", summary: fmt.Sprintf(
				"expected %s %s, and it did not become true within %dms.",
				selector, goal.describe(), timeoutMS)}
			e.add("Selector", strconv.Quote(selector))
			e.add("Snapshot", snapshotAge(snapshot))
			for _, line := range attempts {
				e.log = append(e.log, "  - "+line)
			}
			e.next = "this is a real observation of the current tree, not a stale cache — the state genuinely is not what you expected. Either the action did not land, or the app reports this state under a different name; call get_app_state and read the element's actual [states]."
			return textResult(e.Error(), true)
		}
		time.Sleep(400 * time.Millisecond)
	}
}

// lookupBySelector 按 `<role> "<name>"` / `"<name>"` / `<role>` 找唯一元素。
//
// 语法刻意与快照行**逐字一致**：树里渲染的是 `4 push button "Save" [..]`，
// 那么选择器就写 `push button "Save"`——从行里抄下来即可，不用学第二套写法。
//
// 命中多个时**不挑一个**，而是把候选连同下标一起列出来让调用方收敛。
// 静默挑一个正是本项目一直在修的那类错误。
func lookupBySelector(snapshot *appSnapshot, selector, hint string) (*elementRecord, error) {
	return lookupBySelectorFor(snapshot, selector, "", "")
}

func lookupBySelectorFor(snapshot *appSnapshot, selector, declared, tool string) (*elementRecord, error) {
	role, name, hasName, rest := parseSelector(selector)
	preds, predErr := parsePredicates(rest)
	if predErr != nil {
		e := &toolError{tool: tool, summary: fmt.Sprintf(
			"the selector %q has a trailing part I cannot read: %s.", selector, predErr)}
		e.add("Selector", strconv.Quote(selector))
		e.add("Snapshot", snapshotAge(snapshot))
		e.step("parsed selector as role=%q name=%q", role, name)
		e.step("left over after the name: %q", rest)
		e.next = selectorPredicateHelp
		return nil, e
	}
	// 身份来源有两级，**优先级照 Playwright 的 selector generator**：
	// 它给 role+accessible-name 打 100 分，给 label / alt-text 打 140–160
	// （分数越低越优先），都远好于 nth= 的 10000 分。
	//
	// 我们对应的两级是 name 与 description。加上 description 这一级是实测决定的：
	// 七个应用 704 个节点里，只按 role+name 能唯一指认的占 55%，
	// 加上 description 之后到 66%；GIMP 上从 57 个涨到 114 个（**翻一倍**），
	// 因为它 170 个节点里 95 个有 description、只有 61 个有 name。
	//
	// 顺带记一条**已经查实的死路**：AT-SPI 的 accessible_id（对应 Playwright
	// 打 1 分的 test-id）在这七个应用上**全是 0**，桌面上没有这一级可用。
	var byName, byDescription []elementRecord
	for _, record := range snapshot.Elements {
		if role != "" && !strings.EqualFold(strings.TrimSpace(record.ControlType), role) {
			continue
		}
		if len(preds) > 0 && !recordMatchesPredicates(&record, preds) {
			continue
		}
		if !hasName {
			// role 与谓词都没有时不能放行，否则空选择器匹配全树。
			// 只要有谓词，这条顾虑就不存在了——`[desc="View options"]`
			// 本身就是一条足够窄的身份，实测是 Nautilus 那三个同名
			// `toggle button Menu` 唯一能区分开的写法。
			if role == "" && len(preds) == 0 {
				continue
			}
			byName = append(byName, record)
			continue
		}
		if record.Name == name {
			byName = append(byName, record)
		} else if record.Description == name {
			byDescription = append(byDescription, record)
		}
	}
	matches := byName
	if len(matches) == 0 {
		// name 一个都没匹配上，才退到 description——高优先级有结果时不混入低优先级，
		// 否则一个精确的名字命中会被一堆描述命中稀释成"歧义"。
		matches = byDescription
	}
	if len(matches) == 1 {
		copy := matches[0]
		return &copy, nil
	}
	base := func(summary string) *toolError {
		e := &toolError{tool: tool, summary: summary}
		e.add("Selector", strconv.Quote(selector))
		if declared != "" {
			e.add("Declared", strconv.Quote(declared))
		}
		e.add("Snapshot", snapshotAge(snapshot))
		return e
	}
	if len(matches) == 0 {
		e := base(fmt.Sprintf("no element matches the selector %q.", selector)).
			step("parsed selector as role=%q name=%q%s", role, name, describePredicates(preds)).
			step("searched %d elements by name, then by desc=\"…\"", len(snapshot.Elements))
		e.log = append(e.log, closestElements(snapshot, declared+" "+name, 5)...)
		e.next = "selectors are written exactly as the snapshot renders them, e.g. `push button \"Save\"`, or just `\"Save\"` to match by name alone. The quoted part matches an element's name, or its desc=\"…\" when no name matches. If the UI has changed, call get_app_state again."
		if len(preds) > 0 {
			// 有谓词时最可能的原因是状态已经变了（比如那个框已经不再 checked），
			// 而不是名字抄错了。先说这一条，再说通用建议。
			e.next = "the predicates are matched against the element's current state, which may have changed since the snapshot — try the selector without them first, then call get_app_state to see the state now. " + e.next
		}
		return nil, e
	}

	// 多匹配时**不挑一个**。Playwright 的 strict mode 就是这个纪律，
	// 维护者原话：sometimes there are two elements, and picking the first
	// "would just click the first element on the page… Locators on the other
	// side would throw and the user would directly know that its unexpected
	// instead of clicking on the wrong element."
	// 静默挑一个正是本项目一直在修的那类错误。
	e := base(fmt.Sprintf("the selector %q is ambiguous: it matches %d elements.",
		selector, len(matches)))
	e.step("parsed selector as role=%q name=%q%s", role, name, describePredicates(preds))
	e.step("matched %d elements:", len(matches))
	for i, record := range matches {
		if i == 6 {
			e.log = append(e.log, fmt.Sprintf("    … and %d more", len(matches)-6))
			break
		}
		// 有名字的才给 `aka <选择器>`——无名元素的"替代写法"就是下标本身，
		// 而下标已经在行首了，再写一遍是废话。
		line := fmt.Sprintf("    %-4d %s", record.Index, describeRecord(&record))
		if record.Name != "" {
			line = fmt.Sprintf("    %-4d %-26s aka %s %q",
				record.Index, describeRecord(&record), record.ControlType, record.Name)
		} else if record.Description != "" {
			line = fmt.Sprintf("    %-4d %-26s aka %s %q",
				record.Index, describeRecord(&record), record.ControlType, record.Description)
		}
		e.log = append(e.log, line)
	}
	if role != "" && !hasName {
		e.next = "narrow it by adding the name in quotes, e.g. `" + role + " \"Save\"`, or pass one of the indices above."
	} else {
		e.next = "narrow it by adding the role, e.g. `push button \"Save\"`, or pass one of the indices above."
	}
	// 同名同角色的元素只能靠 desc 区分——实测 Nautilus 里三个 `toggle button
	// "Menu"` 就是这种情况，唯一的区别是 desc="Show operations" / "View options"。
	// 候选列表里已经把它们的 desc 打出来了，这里只需要告诉调用方能拿它来筛。
	//
	// 例子从**真实候选**里拿，拿不到就不给：编一个 desc 出来会让调用方
	// 照抄一个不存在的值，那比不给建议更糟。
	if example := describeSelectorExample(matches); len(preds) == 0 && example != "" {
		e.next += " If the candidates above differ only by desc=\"…\", add it as a predicate: `" +
			example + "`."
	}
	return nil, e
}

// parseSelector 拆出 role、name 与**末引号之后的残串**。
//
// 第四个返回值是补的一个真 bug：原来这里取到 name 就 return，
// `selector[end+1:]` 从头到尾没人看。于是
//
//	push button "Save" [checked]      -> role="push button" name="Save"
//	push button "OK" >> nth=0         -> role="push button" name="OK"
//
// 两条都**静默丢掉约束、照常匹配、还返回成功**。调用方以为自己加了限定，
// 实际拿到的是不带限定的结果——正是本项目反复在修的那类"工具骗 agent"。
//
// 残串交给 parsePredicates：认得的当约束执行，认不得的报错。
func parseSelector(selector string) (string, string, bool, string) {
	selector = strings.TrimSpace(selector)
	start := strings.Index(selector, "\"")
	// 角色段止于**第一个 `[` 或第一个 `"`，谁先来算谁**。
	// 少了这一判，`toggle button [desc="Show operations"]` 会把谓词值的
	// 那个引号当成名字的起点，解析成 role=`toggle button [desc=`——
	// 这是写测试时当场撞出来的。
	if bracket := strings.Index(selector, "["); bracket >= 0 &&
		(start < 0 || bracket < start) {
		return strings.TrimSpace(selector[:bracket]), "", false,
			strings.TrimSpace(selector[bracket:])
	}
	if start < 0 {
		// 没有引号也没有方括号时整串都是 role，不存在残串。
		return selector, "", false, ""
	}
	end := closingQuote(selector, start)
	if end <= start {
		return strings.TrimSpace(selector[:start]), "", false, ""
	}
	name := unescapeQuoted(selector[start+1 : end])
	return strings.TrimSpace(selector[:start]), name,
		true, strings.TrimSpace(selector[end+1:])
}

// closingQuote 找 start 处那个引号的配对引号，跳过 `\"` 转义。
//
// 原来这里用 strings.LastIndex，遇到 `[desc="…"]` 这种后面还有引号的
// 选择器会把整段吞进 name。既然现在残串要参与匹配，配对就必须是真配对。
func closingQuote(text string, start int) int {
	for i := start + 1; i < len(text); i++ {
		if text[i] == '\\' {
			i++
			continue
		}
		if text[i] == '"' {
			return i
		}
	}
	return -1
}

// unescapeQuoted 还原 runtime.py 里 quoted() 做的转义。
func unescapeQuoted(text string) string {
	var out strings.Builder
	for i := 0; i < len(text); i++ {
		if text[i] == '\\' && i+1 < len(text) {
			i++
			out.WriteByte(text[i])
			continue
		}
		out.WriteByte(text[i])
	}
	return out.String()
}

// selectorPredicate 是选择器尾部方括号里的一条约束，语义是 AND。
type selectorPredicate struct {
	kind  string // state | desc | placeholder | action
	value string
}

// 可用的状态词**就是快照会打印的那几个**，一个不多。
// 名单直接对应 runtime.py 的 NOTABLE_STATES 加上 has-click-action。
//
// 刻意不收 [disabled]：state_segment 的注释里记着实测结论——Nautilus 的文件
// 图标根本不设 ENABLED/SENSITIVE 却完全可操作，我们既然不打印它，
// 就更不能让人拿它来筛。也不收 [level]，那是 aria 有我们没有的属性，
// 为了对齐而造一个查不到的谓词只会让 agent 白撞墙。
var selectorStateWords = map[string]bool{
	"checked":          true,
	"expanded":         true,
	"selected":         true,
	"focused":          true,
	"has-click-action": true,
}

const selectorPredicateHelp = "Supported trailing predicates are the ones the snapshot prints: " +
	"[checked] [expanded] [selected] [focused] [has-click-action] " +
	"[desc=\"…\"] [placeholder=\"…\"] [actions=…]. " +
	"The leading index, the {x,y,w,h} box and the trailing : \"value\" are not part of a selector — drop them. " +
	"Chained (>>), positional (nth=) and layout (:right-of) selectors do not exist here."

// parsePredicates 把残串拆成约束。认不出来的一律报错，绝不跳过——
// 静默跳过就是这次要修的那个 bug 本身。
func parsePredicates(rest string) ([]selectorPredicate, error) {
	rest = strings.TrimSpace(rest)
	var out []selectorPredicate
	for i := 0; i < len(rest); {
		if rest[i] == ' ' {
			i++
			continue
		}
		if rest[i] != '[' {
			return nil, fmt.Errorf("expected `[` but found %q", rest[i:])
		}
		end := closingBracket(rest, i)
		if end < 0 {
			return nil, fmt.Errorf("`[` is never closed in %q", rest[i:])
		}
		group := strings.TrimSpace(rest[i+1 : end])
		i = end + 1
		parsed, err := parsePredicateGroup(group)
		if err != nil {
			return nil, err
		}
		out = append(out, parsed...)
	}
	return out, nil
}

// closingBracket 找配对的 `]`，跳过引号内部的那些——
// desc 的值里带 `]` 完全可能（实测 Impress 的描述就是整句自然语言）。
func closingBracket(text string, start int) int {
	inQuote := false
	for i := start + 1; i < len(text); i++ {
		switch {
		case text[i] == '\\' && inQuote:
			i++
		case text[i] == '"':
			inQuote = !inQuote
		case text[i] == ']' && !inQuote:
			return i
		}
	}
	return -1
}

func parsePredicateGroup(group string) ([]selectorPredicate, error) {
	if group == "" {
		return nil, errors.New("`[]` is empty")
	}
	for _, prefix := range []string{"desc=", "placeholder=", "actions="} {
		if !strings.HasPrefix(group, prefix) {
			continue
		}
		value := strings.TrimSpace(strings.TrimPrefix(group, prefix))
		kind := strings.TrimSuffix(prefix, "=")
		if kind == "actions" {
			// actions 在快照里是逗号分隔的裸词，不带引号。
			var out []selectorPredicate
			for _, one := range strings.Split(value, ",") {
				if one = strings.TrimSpace(one); one != "" {
					out = append(out, selectorPredicate{kind: "action", value: one})
				}
			}
			if len(out) == 0 {
				return nil, errors.New("`[actions=]` lists no action")
			}
			return out, nil
		}
		if len(value) < 2 || value[0] != '"' || value[len(value)-1] != '"' {
			return nil, fmt.Errorf("`[%s]` needs a quoted value, as the snapshot prints it", group)
		}
		return []selectorPredicate{{
			kind:  strings.TrimSuffix(kind, "="),
			value: unescapeQuoted(value[1 : len(value)-1]),
		}}, nil
	}
	// 状态在快照里是**一个方括号里的多个词**（`[selected focused]`），
	// 所以一组要拆成多条约束，不是一条。
	var out []selectorPredicate
	for _, word := range strings.Fields(group) {
		if !selectorStateWords[strings.ToLower(word)] {
			return nil, fmt.Errorf("`[%s]` is not a predicate I know", group)
		}
		out = append(out, selectorPredicate{kind: "state", value: strings.ToLower(word)})
	}
	return out, nil
}

// describeSelectorExample 从候选里挑一个**真实存在**的 desc，拼成可直接抄用的
// 选择器。没有任何候选带 desc 时返回空串，由调用方决定不给这条建议。
func describeSelectorExample(matches []elementRecord) string {
	for _, record := range matches {
		if record.Description == "" || record.Description == record.Name {
			continue
		}
		head := record.ControlType
		if record.Name != "" {
			head += " " + strconv.Quote(record.Name)
		}
		return fmt.Sprintf("%s [desc=%s]", head, strconv.Quote(record.Description))
	}
	return ""
}

// describePredicates 把解析结果回显进 Call log。调用方要能一眼看出
// 自己那串方括号被读成了什么——尤其是在筛空的时候。
func describePredicates(preds []selectorPredicate) string {
	if len(preds) == 0 {
		return ""
	}
	parts := make([]string, 0, len(preds))
	for _, pred := range preds {
		if pred.kind == "state" {
			parts = append(parts, pred.value)
			continue
		}
		parts = append(parts, fmt.Sprintf("%s=%q", pred.kind, pred.value))
	}
	return " and predicates [" + strings.Join(parts, "] [") + "]"
}

func recordMatchesPredicates(record *elementRecord, preds []selectorPredicate) bool {
	for _, pred := range preds {
		switch pred.kind {
		case "state":
			// States 存的是渲染好的 " [checked expanded]"，按词比对，
			// 不能用 Contains——那样 "selected" 会命中 "unselected"。
			found := false
			for _, word := range strings.Fields(strings.Trim(record.States, " []")) {
				if strings.EqualFold(word, pred.value) {
					found = true
					break
				}
			}
			if !found {
				return false
			}
		case "desc":
			if record.Description != pred.value {
				return false
			}
		case "placeholder":
			if record.Placeholder != pred.value {
				return false
			}
		case "action":
			found := false
			for _, action := range record.Actions {
				if strings.EqualFold(action, pred.value) {
					found = true
					break
				}
			}
			if !found {
				return false
			}
		}
	}
	return true
}

func runPython(request linuxRequest) (*linuxResponse, error) {
	if runtime.GOOS != "linux" {
		return nil, errors.New("Linux Computer Use runtime requires python3 on Linux")
	}

	tempDir, err := os.MkdirTemp("", "open-computer-use-linux-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tempDir)

	scriptPath := filepath.Join(tempDir, "runtime.py")
	operationPath := filepath.Join(tempDir, "operation.json")
	if err := os.WriteFile(scriptPath, []byte(linuxRuntimeScript), 0o600); err != nil {
		return nil, err
	}
	operationData, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(operationPath, operationData, 0o600); err != nil {
		return nil, err
	}

	// 原为硬编码 30s。大型 a11y 树遍历可能超出，改为可配以便诊断。
	runtimeTimeout := 30 * time.Second
	if raw := os.Getenv("OPEN_COMPUTER_USE_RUNTIME_TIMEOUT_SECONDS"); raw != "" {
		if seconds, convErr := strconv.Atoi(raw); convErr == nil && seconds > 0 {
			runtimeTimeout = time.Duration(seconds) * time.Second
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), runtimeTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, "python3", scriptPath, operationPath)
	cmd.Env = linuxRuntimeEnvironment(os.Environ())
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	output, err := cmd.Output()
	if ctx.Err() == context.DeadlineExceeded {
		return nil, fmt.Errorf("Linux runtime timed out after %s", runtimeTimeout)
	}
	if err != nil {
		text := strings.TrimSpace(stderr.String())
		if text == "" {
			text = strings.TrimSpace(string(output))
		}
		if text == "" {
			text = err.Error()
		}
		return nil, fmt.Errorf("Linux runtime failed: %s", text)
	}

	var response linuxResponse
	if err := json.Unmarshal(output, &response); err != nil {
		return nil, fmt.Errorf("Linux runtime returned invalid JSON: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if note := runtimeStderrNote(stderr.String()); note != "" {
		response.Notes = append(response.Notes, note)
	}
	return &response, nil
}

// runtimeStderrNote 把运行时在**成功路径**上写到 stderr 的东西带出来。
//
// 这里原本只在 err != nil 时看 stderr，成功就直接丢掉。于是 pyatspi 的 DBus
// 超时、GTK 的警告这类信息我们**永远看不到**——树可能因为一次超时而残缺，
// 而响应里一个字都不会提。
//
// 会不会变成噪声？实测过才敢说：gedit / GIMP / VS Code / LibreOffice 四个应用
// 的成功调用，stderr **全部是 0 字节**。所以这不是在给输出加噪声，
// 是在补一条现在完全没有的诊断通道。如果将来真出现每次都刷的良性警告，
// 那时再按实测加过滤规则——现在凭空写一份过滤名单，等于给没见过的东西定罪。
//
// 重复行压成一条：一次遍历几千个节点，同一句警告可能出现上千次，
// 原样带出来会把真正的信息淹掉。
func runtimeStderrNote(raw string) string {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ""
	}
	seen := map[string]int{}
	order := []string{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if _, ok := seen[line]; !ok {
			order = append(order, line)
		}
		seen[line]++
	}
	if len(order) == 0 {
		return ""
	}
	var out strings.Builder
	out.WriteString("[runtime stderr] the Linux runtime succeeded but wrote diagnostics. " +
		"A partial or stale tree can come from these, so read them before trusting a " +
		"surprising snapshot:")
	for index, line := range order {
		if index == 8 {
			fmt.Fprintf(&out, "\n  … and %d more distinct line(s)", len(order)-8)
			break
		}
		if len(line) > 200 {
			line = line[:200] + "…"
		}
		if seen[line] > 1 {
			fmt.Fprintf(&out, "\n  %s (x%d)", line, seen[line])
		} else {
			fmt.Fprintf(&out, "\n  %s", line)
		}
	}
	return out.String()
}

func linuxRuntimeEnvironment(base []string) []string {
	uid := os.Getuid()
	return linuxRuntimeEnvironmentFrom(base, uid, desktopProcessEnvironments(uid))
}

func linuxRuntimeEnvironmentFrom(base []string, uid int, processEnvs []map[string]string) []string {
	env := envSliceToMap(base)
	runtimeDir := chooseRuntimeDir(env, processEnvs, uid)
	if runtimeDir != "" {
		env["XDG_RUNTIME_DIR"] = runtimeDir
	}

	if value := sessionBusAddress(env["DBUS_SESSION_BUS_ADDRESS"], runtimeDir, processEnvs); value != "" {
		env["DBUS_SESSION_BUS_ADDRESS"] = value
	}
	if value := waylandDisplay(env["WAYLAND_DISPLAY"], runtimeDir, processEnvs); value != "" {
		env["WAYLAND_DISPLAY"] = value
	}

	for _, key := range []string{
		"DISPLAY",
		"XAUTHORITY",
		"XDG_CURRENT_DESKTOP",
		"XDG_SESSION_DESKTOP",
		"XDG_SESSION_TYPE",
		"DESKTOP_SESSION",
		"GDK_BACKEND",
		"QT_QPA_PLATFORMTHEME",
		"AT_SPI_BUS_ADDRESS",
	} {
		if strings.TrimSpace(env[key]) == "" {
			if value := firstSessionValue(processEnvs, key); value != "" {
				env[key] = value
			}
		}
	}

	return envMapToSlice(base, env)
}

func chooseRuntimeDir(env map[string]string, processEnvs []map[string]string, uid int) string {
	candidates := []string{env["XDG_RUNTIME_DIR"]}
	for _, processEnv := range processEnvs {
		candidates = append(candidates, processEnv["XDG_RUNTIME_DIR"])
	}
	candidates = append(candidates, fmt.Sprintf("/run/user/%d", uid))

	seen := map[string]bool{}
	for _, candidate := range candidates {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			continue
		}
		candidate = filepath.Clean(candidate)
		if seen[candidate] {
			continue
		}
		seen[candidate] = true
		if validRuntimeDir(candidate, uid) {
			return candidate
		}
	}
	return ""
}

func sessionBusAddress(current, runtimeDir string, processEnvs []map[string]string) string {
	current = strings.TrimSpace(current)
	if runtimeDir != "" {
		busPath := filepath.Join(runtimeDir, "bus")
		if isSocket(busPath) && shouldUseRuntimeBus(current, runtimeDir) {
			return "unix:path=" + busPath
		}
	}
	if current != "" {
		return current
	}
	for _, processEnv := range processEnvs {
		value := strings.TrimSpace(processEnv["DBUS_SESSION_BUS_ADDRESS"])
		if value == "" {
			continue
		}
		if runtimeDir != "" {
			busPath := filepath.Join(runtimeDir, "bus")
			if isSocket(busPath) && strings.Contains(value, busPath) {
				return "unix:path=" + busPath
			}
		}
		return value
	}
	if runtimeDir != "" {
		busPath := filepath.Join(runtimeDir, "bus")
		if isSocket(busPath) {
			return "unix:path=" + busPath
		}
	}
	return ""
}

func shouldUseRuntimeBus(current, runtimeDir string) bool {
	current = strings.TrimSpace(current)
	if current == "" {
		return true
	}
	busPath := filepath.Join(runtimeDir, "bus")
	if strings.Contains(current, busPath) {
		return true
	}
	return strings.Contains(current, "/run/user/") && !strings.Contains(current, runtimeDir)
}

func waylandDisplay(current, runtimeDir string, processEnvs []map[string]string) string {
	if value := normalizeWaylandDisplay(current, runtimeDir); value != "" {
		return value
	}
	for _, processEnv := range processEnvs {
		if value := normalizeWaylandDisplay(processEnv["WAYLAND_DISPLAY"], runtimeDir); value != "" {
			return value
		}
	}
	if runtimeDir == "" {
		return ""
	}
	return firstWaylandSocket(runtimeDir)
}

func normalizeWaylandDisplay(value, runtimeDir string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if runtimeDir == "" {
		return value
	}
	if filepath.IsAbs(value) {
		if isSocket(value) {
			return value
		}
		return ""
	}
	if isSocket(filepath.Join(runtimeDir, value)) {
		return value
	}
	return ""
}

func firstWaylandSocket(runtimeDir string) string {
	for _, name := range []string{"wayland-0", "wayland-1"} {
		if isSocket(filepath.Join(runtimeDir, name)) {
			return name
		}
	}
	matches, err := filepath.Glob(filepath.Join(runtimeDir, "wayland-*"))
	if err != nil {
		return ""
	}
	sort.Strings(matches)
	for _, match := range matches {
		if strings.HasSuffix(match, ".lock") {
			continue
		}
		if isSocket(match) {
			return filepath.Base(match)
		}
	}
	return ""
}

func firstSessionValue(processEnvs []map[string]string, key string) string {
	for _, processEnv := range processEnvs {
		if value := strings.TrimSpace(processEnv[key]); value != "" {
			return value
		}
	}
	return ""
}

type rankedProcessEnv struct {
	env  map[string]string
	rank int
	pid  int
}

func desktopProcessEnvironments(uid int) []map[string]string {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil
	}

	var candidates []rankedProcessEnv
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			continue
		}
		procDir := filepath.Join("/proc", entry.Name())
		if !pathOwnedByUID(procDir, uid) {
			continue
		}
		rank := desktopProcessRank(processSearchText(procDir))
		if rank == 0 {
			continue
		}
		processEnv := readProcEnviron(procDir)
		if !hasSessionEnvSignal(processEnv) {
			continue
		}
		candidates = append(candidates, rankedProcessEnv{
			env:  processEnv,
			rank: rank + sessionEnvRank(processEnv),
			pid:  pid,
		})
	}

	sort.SliceStable(candidates, func(i, j int) bool {
		if candidates[i].rank != candidates[j].rank {
			return candidates[i].rank > candidates[j].rank
		}
		return candidates[i].pid < candidates[j].pid
	})

	results := make([]map[string]string, 0, len(candidates))
	for _, candidate := range candidates {
		results = append(results, candidate.env)
	}
	return results
}

func processSearchText(procDir string) string {
	var parts []string
	if data, err := os.ReadFile(filepath.Join(procDir, "comm")); err == nil {
		parts = append(parts, string(bytes.TrimSpace(data)))
	}
	if data, err := os.ReadFile(filepath.Join(procDir, "cmdline")); err == nil {
		data = bytes.Trim(data, "\x00")
		parts = append(parts, strings.ReplaceAll(string(data), "\x00", " "))
	}
	return strings.ToLower(strings.Join(parts, " "))
}

func desktopProcessRank(text string) int {
	patterns := []struct {
		needle string
		rank   int
	}{
		{"gnome-session", 100},
		{"gnome-shell", 95},
		{"plasmashell", 95},
		{"kwin_wayland", 95},
		{"kwin_x11", 95},
		{"startplasma", 95},
		{"cinnamon-session", 95},
		{"mate-session", 95},
		{"xfce4-session", 95},
		{"lxqt-session", 95},
		{"sway", 95},
		{"wayfire", 95},
		{"xorg", 80},
		{"xwayland", 75},
		{"gnome-terminal-server", 65},
		{"ptyxis", 65},
		{"kgx", 65},
		{"konsole", 65},
		{"xfce4-terminal", 65},
		{"alacritty", 65},
		{"wezterm", 65},
		{"kitty", 65},
		{"tilix", 65},
		{"codex", 50},
		{"dbus-daemon", 45},
		{"systemd --user", 40},
	}

	rank := 0
	for _, pattern := range patterns {
		if strings.Contains(text, pattern.needle) && pattern.rank > rank {
			rank = pattern.rank
		}
	}
	return rank
}

func sessionEnvRank(env map[string]string) int {
	rank := 0
	for _, key := range []string{"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} {
		if strings.TrimSpace(env[key]) != "" {
			rank += 20
		}
	}
	for _, key := range []string{"DISPLAY", "WAYLAND_DISPLAY"} {
		if strings.TrimSpace(env[key]) != "" {
			rank += 10
		}
	}
	if strings.TrimSpace(env["XAUTHORITY"]) != "" {
		rank += 5
	}
	return rank
}

func hasSessionEnvSignal(env map[string]string) bool {
	for _, key := range []string{
		"XDG_RUNTIME_DIR",
		"DBUS_SESSION_BUS_ADDRESS",
		"DISPLAY",
		"WAYLAND_DISPLAY",
		"XAUTHORITY",
		"AT_SPI_BUS_ADDRESS",
	} {
		if strings.TrimSpace(env[key]) != "" {
			return true
		}
	}
	return false
}

func readProcEnviron(procDir string) map[string]string {
	data, err := os.ReadFile(filepath.Join(procDir, "environ"))
	if err != nil {
		return nil
	}
	return parseNullEnv(data)
}

func parseNullEnv(data []byte) map[string]string {
	env := map[string]string{}
	for _, entry := range bytes.Split(data, []byte{0}) {
		if len(entry) == 0 {
			continue
		}
		key, value, ok := strings.Cut(string(entry), "=")
		if ok && key != "" {
			env[key] = value
		}
	}
	return env
}

func envSliceToMap(items []string) map[string]string {
	env := map[string]string{}
	for _, item := range items {
		key, value, ok := strings.Cut(item, "=")
		if ok && key != "" {
			env[key] = value
		}
	}
	return env
}

func envMapToSlice(base []string, env map[string]string) []string {
	items := make([]string, 0, len(env))
	seen := map[string]bool{}
	for _, item := range base {
		key, _, ok := strings.Cut(item, "=")
		if !ok || key == "" {
			items = append(items, item)
			continue
		}
		if value, ok := env[key]; ok {
			items = append(items, key+"="+value)
			seen[key] = true
		}
	}

	var added []string
	for key := range env {
		if !seen[key] {
			added = append(added, key)
		}
	}
	sort.Strings(added)
	for _, key := range added {
		items = append(items, key+"="+env[key])
	}
	return items
}

func validRuntimeDir(path string, uid int) bool {
	info, err := os.Stat(path)
	if err != nil || !info.IsDir() {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return false
	}
	return int(stat.Uid) == uid
}

func pathOwnedByUID(path string, uid int) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return false
	}
	return int(stat.Uid) == uid
}

func isSocket(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode()&os.ModeSocket != 0
}

func requiredString(args map[string]any, key string) string {
	value, _ := args[key].(string)
	return strings.TrimSpace(value)
}

func optionalString(args map[string]any, key string) string {
	value, _ := args[key].(string)
	return value
}

func requiredElementIndex(args map[string]any) string {
	return strings.TrimSpace(optionalElementIndex(args))
}

func optionalElementIndex(args map[string]any) string {
	return elementIndexString(args["element_index"])
}

func elementIndexString(value any) string {
	switch value := value.(type) {
	case string:
		return value
	case json.Number:
		if integer, err := value.Int64(); err == nil {
			return strconv.FormatInt(integer, 10)
		}
		if float, err := value.Float64(); err == nil {
			return integerElementIndexFloat(float)
		}
	case float64:
		return integerElementIndexFloat(value)
	case int:
		return strconv.Itoa(value)
	case int64:
		return strconv.FormatInt(value, 10)
	}
	return ""
}

func integerElementIndexFloat(value float64) string {
	if math.IsNaN(value) || math.IsInf(value, 0) || math.Trunc(value) != value {
		return ""
	}
	return strconv.FormatInt(int64(value), 10)
}

func requiredFloat(args map[string]any, key string) *float64 {
	return optionalFloat(args, key)
}

func optionalFloat(args map[string]any, key string) *float64 {
	switch value := args[key].(type) {
	case float64:
		return &value
	case int:
		float := float64(value)
		return &float
	case json.Number:
		float, err := value.Float64()
		if err == nil {
			return &float
		}
	}
	return nil
}

func optionalTextLimit(args map[string]any, key string) (*textLimit, error) {
	value, ok := args[key]
	if !ok {
		return nil, nil
	}
	return textLimitFromValue(value, key)
}

func textLimitFromValue(value any, key string) (*textLimit, error) {
	if stringValue, ok := value.(string); ok {
		if strings.EqualFold(stringValue, "max") {
			return &textLimit{max: true}, nil
		}
		return nil, fmt.Errorf("%s must be a positive integer or max", key)
	}
	integer, err := positiveIntFromValue(value, key)
	if err != nil {
		return nil, fmt.Errorf("%s must be a positive integer or max", key)
	}
	return &textLimit{count: *integer}, nil
}

func optionalPositiveInt(args map[string]any, key string) (*int, error) {
	value, ok := args[key]
	if !ok {
		return nil, nil
	}
	return positiveIntFromValue(value, key)
}

func positiveIntFromValue(value any, key string) (*int, error) {
	switch typed := value.(type) {
	case int:
		return positiveIntFromInt64(int64(typed), key)
	case float64:
		if !isWholeNumber(typed) {
			return nil, fmt.Errorf("%s must be a positive integer", key)
		}
		return positiveIntFromFloat64(typed, key)
	case json.Number:
		integer, err := typed.Int64()
		if err != nil {
			return nil, fmt.Errorf("%s must be a positive integer", key)
		}
		return positiveIntFromInt64(integer, key)
	default:
		return nil, fmt.Errorf("%s must be a positive integer", key)
	}
}

func positiveIntFromFloat64(value float64, key string) (*int, error) {
	if !isWholeNumber(value) || value <= 0 || value > float64(maxInt()) {
		return nil, fmt.Errorf("%s must be a positive integer", key)
	}
	integer := int(value)
	return &integer, nil
}

func positiveIntFromInt64(value int64, key string) (*int, error) {
	if value <= 0 || value > int64(maxInt()) {
		return nil, fmt.Errorf("%s must be a positive integer", key)
	}
	integer := int(value)
	return &integer, nil
}

func isWholeNumber(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && math.Trunc(value) == value
}

func maxInt() int {
	return int(^uint(0) >> 1)
}

func intValue(value *float64, fallback int) int {
	if value == nil {
		return fallback
	}
	return int(*value)
}

func floatValue(value *float64, fallback float64) float64 {
	if value == nil {
		return fallback
	}
	return *value
}

func defaultString(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func parseClickMethod(value string) (string, error) {
	normalized := strings.ToLower(strings.TrimSpace(value))
	if normalized == "" {
		return "auto", nil
	}
	for _, candidate := range clickMethodValues {
		if normalized == candidate {
			return normalized, nil
		}
	}
	return "", fmt.Errorf("Invalid click_method %q. Expected one of: %s", value, strings.Join(clickMethodValues, ", "))
}

func globalPointerFallbacksEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// 通道能力开关，对齐 Playwright 的 `--caps=vision`。
//
// `OPEN_COMPUTER_USE_CHANNELS` 逗号分隔，默认三条全开：a11y, gui, keyboard。
// 关掉某条通道时，**它的工具根本不出现在 tools/list 里**——不是调用时才拒绝。
// 这个区别很要紧：模型看得见的工具会去试，试了被拒就是浪费一轮；
// 看不见就不会试。Playwright 把坐标做成 opt-in 能力也是这个道理。
//
// 用途有二：
//  1. `#29` 的 A/B 需要一个能真正**关掉**一条通道的开关，而不只是关掉截图
//  2. 部署到只信任语义动作的环境时，可以整条关掉坐标合成
func enabledChannels() map[string]bool {
	raw := strings.TrimSpace(os.Getenv("OPEN_COMPUTER_USE_CHANNELS"))
	if raw == "" {
		return map[string]bool{"a11y": true, "gui": true, "keyboard": true}
	}
	enabled := map[string]bool{}
	for _, part := range strings.Split(raw, ",") {
		part = strings.ToLower(strings.TrimSpace(part))
		if part != "" {
			enabled[part] = true
		}
	}
	return enabled
}

// 每个工具属于哪条通道。list_apps 不属于任何通道（它只枚举应用），永远可用。
// appArgumentHelp 是**所有**接受 app 参数的工具共用的说明。
//
// 为什么值得写这么长：13 条真实 agent 轨迹里，**12 条**的开局都是
// `list_apps → get_app_state`——agent 不敢直接猜应用名，于是每道题都先花一步
// 去列一遍。还有一次直接报了 appNotFound("google-chrome")，而那个应用叫
// "Google Chrome"。
//
// 根因是**这台机器上的 a11y 应用名同时存在三种风格，没有任何规则能推出来**
// （实测）：
//
//	显示名     "Google Chrome"        进程却叫 chrome
//	二进制名   "vlc" "gedit" "code"   与进程同名
//	app-id     "org.gnome.Nautilus"   进程却叫 nautilus
//	           "org.gnome.Software"   进程却叫 snap-store
//
// 既然规则推不出来，就直接把规则告诉调用方，并说明匹配有多宽松——
// 让它敢于第一次就猜，猜错也能从错误里拿到候选名单。
const appArgumentHelp = "Which application to act on. Matching is deliberately " +
	"forgiving: case-insensitive, separator-insensitive, and substring-based, and " +
	"it also matches window titles and a numeric PID. So \"google-chrome\", " +
	"\"Google Chrome\" and \"chrome\" all find the same app. " +
	"Names on Linux follow three different conventions with no rule to tell them " +
	"apart, so just guess the obvious one: a display name (\"Google Chrome\", " +
	"\"Thunderbird\"), a binary name (\"vlc\", \"gedit\", \"code\"), or a " +
	"reverse-DNS app id (\"org.gnome.Nautilus\" — the Files manager, whose " +
	"process is just `nautilus`). A wrong guess is cheap: the error lists every " +
	"application currently visible, so you rarely need list_apps first."

var toolChannel = map[string]string{
	"get_app_state":         "a11y",
	"find":                  "a11y",
	"verify":                "a11y",
	"click":                 "a11y",
	"invoke_element_action": "a11y",
	"set_value":             "a11y",
	"get_screenshot":        "gui",
	"click_xy":              "gui",
	"drag_xy":               "gui",
	"press_key":             "keyboard",
	"type_text":             "keyboard",
	"scroll":                "keyboard",
}

func sortedChannels() []string {
	enabled := enabledChannels()
	names := make([]string, 0, len(enabled))
	for name := range enabled {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func toolIsEnabled(name string) bool {
	channel, known := toolChannel[name]
	if !known {
		return true
	}
	return enabledChannels()[channel]
}

func toolDefinitions() []toolDefinition {
	all := allToolDefinitions()
	kept := make([]toolDefinition, 0, len(all))
	for _, tool := range all {
		if toolIsEnabled(tool.Name) {
			kept = append(kept, tool)
		}
	}
	return kept
}

func allToolDefinitions() []toolDefinition {
	return []toolDefinition{
		{
			Name:        "click",
			Description: "CHANNEL: ACCESSIBILITY. Click an element addressed by element_index from get_app_state. This invokes the element's own accessibility action when it has one, which does NOT steal focus from whatever the user is doing; when it has none, it synthesizes a click at coordinates taken FROM THE TREE, so the target is still identified rather than guessed. It does not accept x/y \u2014 for a click addressed by pixel, use click_xy. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty(appArgumentHelp),
				"element_index": stringProperty("Either an index from the most recent get_app_state, or a SELECTOR written exactly as the snapshot renders it, e.g. `push button \"Save\"` (or just `\"Save\"` to match by name alone). When several elements share a role and name, append one of the predicates the snapshot prints for them: `toggle button \"Menu\" [desc=\"View options\"]`, `check menu item \"Ruler\" [checked]`. Selectors survive the renumbering that happens whenever the UI changes; indices do not."),
				"click_count":   integerProperty("Number of clicks. Defaults to 1"),
				"mouse_button":  enumStringProperty("Mouse button to click. Defaults to left.", []string{"left", "right", "middle"}),
				"click_method":  enumStringProperty("Click implementation: auto (default), accessibility, app_post, sky_click, or global. Linux supports global AT-SPI mouse synthesis and does not currently support app_post or sky_click.", clickMethodValues),
			}, []string{"app", "element_index"}),
		},
		{
			Name:        "find",
			Description: "CHANNEL: ACCESSIBILITY. Locate elements WITHOUT dumping the whole tree. Filters are ANDed and matched as case-insensitive substrings; the matching lines come back verbatim from the tree, so element_index is directly usable by click / set_value / invoke_element_action. Use this instead of get_app_state whenever you already know what you are looking for — a VS Code tree is over 15000 characters and reading all of it to press one button is waste. Honest caveat: this does NOT make the machine faster. The runtime still walks the entire accessibility tree, because the desktop has no query that can be pushed down into the app the way a CSS selector is pushed into a browser. What it saves is YOUR context, which is the actual bottleneck. Returns no screenshot — it is a query, not an observation; call get_screenshot if you need pixels. This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":   stringProperty(appArgumentHelp),
				"role":  stringProperty("Match the element role, e.g. \"button\" matches both `push button` and `toggle button`. Substring, case-insensitive."),
				"name":  stringProperty("Match the element's accessible name, e.g. \"save\" finds `push button \"Save As…\"`. Substring, case-insensitive."),
				"text":  stringProperty("Match name OR desc OR value OR placeholder — use this when you do not know which field carries the string. Which field it lands in is genuinely unstable across toolkits: the same search box reports it as placeholder under GTK and as name under Electron."),
				"state": stringProperty("Match the reported states, e.g. \"focused\", \"checked\", \"modal\". Substring, case-insensitive."),
				"limit": integerProperty("Maximum matches to return. Defaults to 20."),
			}, []string{"app"}),
		},
		{
			Name:        "verify",
			Description: "CHANNEL: ACCESSIBILITY. Assert something about an element and RETRY until it becomes true or the timeout expires. This is the difference between asking once and waiting for a result: a single get_app_state read a beat too early gives you the wrong answer with full confidence. Use it after an action to confirm the effect actually landed, rather than assuming a successful tool call means a successful action. Costs one full tree walk per poll (400ms apart, 5s default), so keep the timeout tight. On failure it returns the observation from every attempt — what the states ACTUALLY were, not just that the assertion failed. It never touches the UI. This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":            stringProperty(appArgumentHelp),
				"element_index":  stringProperty("Index from the last get_app_state, or a selector written exactly as the snapshot renders it, e.g. `push button \"Save\"`. Prefer the selector here: verify re-reads the tree on every poll, and a selector survives the renumbering that a changing UI causes."),
				"state":          stringProperty("Expected state, e.g. \"checked\", \"focused\", \"showing\". Prefix with ! to assert its absence, e.g. \"!checked\"."),
				"value_contains": stringProperty("Expected substring of the element's value."),
				"text_contains":  stringProperty("Expected substring of name, desc, value, or placeholder."),
				"exists":         booleanProperty("true asserts the element is present; false asserts it is gone — the way to wait for a dialog to close."),
				"timeout_ms":     integerProperty("How long to keep retrying. Defaults to 5000."),
			}, []string{"app", "element_index"}),
		},
		{
			Name:        "click_xy",
			Description: "CHANNEL: GUI. Click at a pixel coordinate. This addresses NO element \u2014 whatever happens to be under that point receives the click \u2014 so reach for it only when the target has no element_index in the tree, or when an accessibility action reported success without doing anything. Coordinates are window-relative and are the SAME space as the attached screenshot and as the Frame values in the tree, so a point read off the image can be passed straight in. Always returns a screenshot, and reports which element the hit test found under that point (a hint, not proof). This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":          stringProperty(appArgumentHelp),
				"x":            numberProperty("X coordinate in window-relative pixels, same space as the screenshot"),
				"y":            numberProperty("Y coordinate in window-relative pixels, same space as the screenshot"),
				"click_count":  integerProperty("Number of clicks. Defaults to 1"),
				"mouse_button": enumStringProperty("Mouse button to click. Defaults to left.", []string{"left", "right", "middle"}),
			}, []string{"app", "x", "y"}),
		},
		{
			Name:        "drag_xy",
			Description: "CHANNEL: GUI. Drag from one pixel coordinate to another. There is NO element-addressed form of drag, and that is deliberate rather than an omission: the DESTINATION of a drag is usually not an element at all (\"move this 15cm down the slide\"). Coordinates are window-relative, the same space as the screenshot. Always returns a screenshot, because the accessibility tree does not reflect drag results \u2014 measured on LibreOffice Impress, moving a title from 0.76cm to 15.00cm left that element's Frame in the tree completely unchanged. Judge the result from the image. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":    stringProperty(appArgumentHelp),
				"from_x": numberProperty("Start X in window-relative pixels"),
				"from_y": numberProperty("Start Y in window-relative pixels"),
				"to_x":   numberProperty("End X in window-relative pixels"),
				"to_y":   numberProperty("End Y in window-relative pixels"),
			}, []string{"app", "from_x", "from_y", "to_x", "to_y"}),
		},
		{
			Name:        "get_app_state",
			Description: "CHANNEL: ACCESSIBILITY (with a screenshot attached). Get the state of an already running app's key window and return its accessibility tree. This does NOT return a screenshot — use get_screenshot for that, and only when the tree is insufficient. This must be called once per assistant turn before interacting with the app. This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":            stringProperty(appArgumentHelp),
				"text_limit":     textLimitProperty("Maximum text characters to return. Use \"max\" for full text. Defaults to 500."),
				"max_tree_nodes": positiveIntegerProperty("Maximum accessibility tree nodes to render. Defaults to 1200."),
				"max_tree_depth": positiveIntegerProperty("Maximum accessibility tree depth to render. Defaults to 64."),
				"boxes":          booleanProperty("Defaults to false. When true, each element line ends with its rectangle as {x,y,width,height} in window-relative pixels. Off by default because the geometry costs 16-25% of the tree and is redundant: a screenshot is attached to every snapshot and uses the SAME coordinate space, so read points off the image; and click(element_index) resolves coordinates server-side without needing them rendered. Turn it on when you want to reason about layout numerically."),
				"prune":          booleanProperty("Defaults to true. Pruning keeps only interactable, on-screen elements and cuts the tree to roughly a fifth without losing anything you can act on; the omission notice reports how many nodes were left out. Set false only if you suspect a needed element was filtered."),
			}, []string{"app"}),
		},
		{
			Name:        "get_screenshot",
			Description: "CHANNEL: GUI. Take a screenshot of an app's key window WITHOUT the accessibility tree. get_app_state already returns a screenshot next to the tree, so reach for this one only when you want the image alone — re-checking a pixel-level detail, watching a canvas change, or confirming an effect that never reaches the tree — and do not want to pay for the tree again. Costs are not ordered the way you might assume: a window screenshot runs about a thousand tokens, more than a small app's tree (gedit ~350) but LESS than a content-rich one (a file manager tree ~2100). This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app": stringProperty(appArgumentHelp),
			}, []string{"app"}),
		},
		{
			Name:        "list_apps",
			Description: "CHANNEL: none — this only enumerates apps. List the apps on this computer. Returns the set of apps that are currently running, as well as any that have been used in the last 14 days, including details on usage frequency. This tool is part of plugin `Computer Use`. You usually do NOT need this before get_app_state: app names are matched case- and separator-insensitively by substring, and a failed lookup already lists every visible application. Reach for list_apps when you want PIDs and window titles, or when a guess failed twice.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{}, nil),
		},
		{
			Name:        "invoke_element_action",
			Description: "CHANNEL: ACCESSIBILITY. Invoke a named accessibility action on an element — a first-class way to drive the UI, not a fallback. `click` already performs each element's default action; this tool reaches the other actions it exposes (e.g. menu, expand, increment). Like click-by-index it does not steal focus, and it is preferred over coordinate clicks whenever the action you need is listed under \"More actions\" in the accessibility tree. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty(appArgumentHelp),
				"element_index": stringProperty("Element identifier"),
				"action":        stringProperty("Secondary accessibility action name"),
			}, []string{"app", "element_index", "action"}),
		},
		{
			Name:        "press_key",
			Description: "CHANNEL: KEYBOARD — the key goes to whatever widget currently holds focus inside the window, NOT to any element you name. Press a key or key-combination on the keyboard, including modifier and navigation keys.\n  - This supports xdotool's `key` syntax.\n  - Examples: \"a\", \"Return\", \"Tab\", \"super+c\", \"Up\", \"KP_0\" (for the numpad 0). This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app": stringProperty(appArgumentHelp),
				"key": stringProperty("Key or key-combination to press"),
			}, []string{"app", "key"}),
		},
		{
			Name:        "scroll",
			Description: "CHANNEL: ACCESSIBILITY for vertical scrolling, KEYBOARD for horizontal. Scroll by a number of pages. Vertical scrolling moves the pointer over the element addressed by element_index and sends wheel notches there, so it IS targeted — measured on gedit with a 600-line file, six notches changed 23% of the text area while doing nothing changed 0%. Two things to keep in mind: the wheel acts on the scrollable ancestor under that point, which is not necessarily the element you named; and one page is APPROXIMATED as 5 notches, so pages=1 is not exactly one Page_Down. Horizontal scrolling still synthesizes Left/Right keys to whatever holds focus and does NOT target the element — the wheel's horizontal buttons are untested here, and this project does not ship paths it has not measured. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty(appArgumentHelp),
				"direction":     stringProperty("Scroll direction: up, down, left, or right"),
				"element_index": stringProperty("Element identifier"),
				"pages":         numberProperty("Number of pages to scroll. Fractional values are supported. Defaults to 1"),
			}, []string{"app", "element_index", "direction"}),
		},
		{
			Name:        "set_value",
			Description: "CHANNEL: ACCESSIBILITY. Set the value of a settable accessibility element. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty(appArgumentHelp),
				"element_index": stringProperty("Element identifier"),
				"value":         stringProperty("Value to assign"),
			}, []string{"app", "element_index", "value"}),
		},
		{
			Name:        "type_text",
			Description: "CHANNEL: KEYBOARD (falls back from ACCESSIBILITY) — this first tries the AT-SPI editable-text API on the focused editable control, and only synthesizes keystrokes if that write does not land. Type literal text using keyboard input. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":  stringProperty(appArgumentHelp),
				"text": stringProperty("Literal text to type"),
			}, []string{"app", "text"}),
		},
	}
}

func objectSchema(properties map[string]any, required []string) map[string]any {
	schema := map[string]any{
		"type":                 "object",
		"properties":           properties,
		"additionalProperties": false,
	}
	if len(required) > 0 {
		schema["required"] = required
	}
	return schema
}

func defaultAnnotations() map[string]any {
	return map[string]any{"destructiveHint": false, "openWorldHint": false}
}

func readOnlyAnnotations() map[string]any {
	return map[string]any{"destructiveHint": false, "idempotentHint": true, "openWorldHint": false, "readOnlyHint": true}
}

func stringProperty(description string) map[string]any {
	return map[string]any{"type": "string", "description": description}
}

func enumStringProperty(description string, values []string) map[string]any {
	property := stringProperty(description)
	property["enum"] = values
	return property
}

func numberProperty(description string) map[string]any {
	return map[string]any{"type": "number", "description": description}
}

func integerProperty(description string) map[string]any {
	return map[string]any{"type": "integer", "description": description}
}

func optionalBool(args map[string]any, key string) *bool {
	value, ok := args[key]
	if !ok {
		return nil
	}
	if flag, ok := value.(bool); ok {
		return &flag
	}
	if text, ok := value.(string); ok {
		flag := text == "true"
		return &flag
	}
	return nil
}

func booleanProperty(description string) map[string]any {
	return map[string]any{"type": "boolean", "description": description}
}

func positiveIntegerProperty(description string) map[string]any {
	return map[string]any{"type": "integer", "minimum": 1, "description": description}
}

func textLimitProperty(description string) map[string]any {
	return map[string]any{
		"anyOf": []any{
			map[string]any{"type": "integer", "minimum": 1},
			map[string]any{"type": "string", "enum": []string{"max"}},
		},
		"description": description,
	}
}

func main() {
	if err := runCLI(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func runCLI(args []string, stdout io.Writer) error {
	if len(args) == 0 {
		fmt.Fprint(stdout, helpText(""))
		return nil
	}

	switch args[0] {
	case "-h", "--help", "help":
		topic := ""
		if len(args) > 1 {
			topic = args[1]
		}
		fmt.Fprint(stdout, helpText(topic))
		return nil
	case "-v", "--version", "version":
		fmt.Fprintln(stdout, version)
		return nil
	case "mcp":
		return runMCP(os.Stdin, stdout)
	case "doctor":
		fmt.Fprintln(stdout, "Linux runtime: AT-SPI2 and GDK run against the signed-in desktop user's accessibility session. When Codex starts without XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, or display variables, open-computer-use tries to discover the same user's session from /run/user/<uid> and desktop processes.")
		return nil
	case "list-apps":
		result := newService().callTool("list_apps", map[string]any{})
		if result.IsError {
			return errors.New(result.Content[0].Text)
		}
		fmt.Fprintln(stdout, result.Content[0].Text)
		return nil
	case "snapshot":
		app, textLimit, maxTreeNodes, maxTreeDepth, err := parseSnapshotArgs(args[1:])
		if err != nil {
			return err
		}
		toolArgs := map[string]any{
			"app": app,
		}
		if textLimit != nil {
			toolArgs["text_limit"] = textLimit.runtimeValue()
		}
		if maxTreeNodes != nil {
			toolArgs["max_tree_nodes"] = *maxTreeNodes
		}
		if maxTreeDepth != nil {
			toolArgs["max_tree_depth"] = *maxTreeDepth
		}
		result := newService().callTool("get_app_state", toolArgs)
		if result.IsError {
			return errors.New(result.Content[0].Text)
		}
		fmt.Fprintln(stdout, result.Content[0].Text)
		return nil
	case "call":
		output, hasError, err := runCallCommand(args[1:], newService())
		if err != nil {
			return err
		}
		encoded, err := json.MarshalIndent(output, "", "  ")
		if err != nil {
			return err
		}
		fmt.Fprintln(stdout, string(encoded))
		if hasError {
			return errors.New("tool call returned isError=true")
		}
		return nil
	default:
		return fmt.Errorf("unknown command: %s\n\n%s", args[0], helpText(""))
	}
}

func parseSnapshotArgs(args []string) (string, *textLimit, *int, *int, error) {
	var app string
	var textLimit *textLimit
	var maxTreeNodes *int
	var maxTreeDepth *int
	for index := 0; index < len(args); index++ {
		arg := args[index]
		switch arg {
		case "--text-limit":
			index++
			if index >= len(args) {
				return "", nil, nil, nil, errors.New("--text-limit requires a positive integer or max value")
			}
			value, err := parseTextLimitOption(args[index], "--text-limit")
			if err != nil {
				return "", nil, nil, nil, err
			}
			textLimit = value
		case "--max-tree-nodes":
			index++
			if index >= len(args) {
				return "", nil, nil, nil, errors.New("--max-tree-nodes requires a positive integer value")
			}
			value, err := parsePositiveIntegerOption(args[index], "--max-tree-nodes")
			if err != nil {
				return "", nil, nil, nil, err
			}
			maxTreeNodes = &value
		case "--max-tree-depth":
			index++
			if index >= len(args) {
				return "", nil, nil, nil, errors.New("--max-tree-depth requires a positive integer value")
			}
			value, err := parsePositiveIntegerOption(args[index], "--max-tree-depth")
			if err != nil {
				return "", nil, nil, nil, err
			}
			maxTreeDepth = &value
		default:
			if strings.HasPrefix(arg, "-") {
				return "", nil, nil, nil, fmt.Errorf("unknown snapshot option: %s", arg)
			}
			if app != "" {
				return "", nil, nil, nil, errors.New("snapshot accepts exactly one app name, process name, window title, or pid")
			}
			app = arg
		}
	}
	if app == "" {
		return "", nil, nil, nil, errors.New("snapshot requires an app name, process name, window title, or pid")
	}
	return app, textLimit, maxTreeNodes, maxTreeDepth, nil
}

func parseTextLimitOption(value, option string) (*textLimit, error) {
	if strings.EqualFold(value, "max") {
		return &textLimit{max: true}, nil
	}
	integer, err := strconv.Atoi(value)
	if err != nil || integer <= 0 {
		return nil, fmt.Errorf("%s must be a positive integer or max", option)
	}
	return &textLimit{count: integer}, nil
}

func parsePositiveIntegerOption(value, option string) (int, error) {
	integer, err := strconv.Atoi(value)
	if err != nil || integer <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", option)
	}
	return integer, nil
}

func runCallCommand(args []string, svc *service) (any, bool, error) {
	if len(args) == 0 {
		return nil, false, errors.New("call requires a tool name or --calls/--calls-file")
	}

	var toolName, argsJSON, argsFile, callsJSON, callsFile string
	for index := 0; index < len(args); index++ {
		arg := args[index]
		switch arg {
		case "--args":
			index++
			if index >= len(args) {
				return nil, false, errors.New("--args requires a value")
			}
			argsJSON = args[index]
		case "--args-file":
			index++
			if index >= len(args) {
				return nil, false, errors.New("--args-file requires a value")
			}
			argsFile = args[index]
		case "--calls":
			index++
			if index >= len(args) {
				return nil, false, errors.New("--calls requires a value")
			}
			callsJSON = args[index]
		case "--calls-file":
			index++
			if index >= len(args) {
				return nil, false, errors.New("--calls-file requires a value")
			}
			callsFile = args[index]
		default:
			if strings.HasPrefix(arg, "-") {
				return nil, false, fmt.Errorf("unknown call option: %s", arg)
			}
			if toolName != "" {
				return nil, false, errors.New("call accepts at most one tool name")
			}
			toolName = arg
		}
	}

	if callsJSON != "" || callsFile != "" {
		if toolName != "" || argsJSON != "" || argsFile != "" {
			return nil, false, errors.New("call sequence does not accept a tool name, --args, or --args-file")
		}
		calls, err := readCallSequence(callsJSON, callsFile)
		if err != nil {
			return nil, false, err
		}
		var outputs []map[string]any
		hasError := false
		for _, call := range calls {
			result := svc.callTool(call.Tool, call.Args)
			outputs = append(outputs, map[string]any{"tool": call.Tool, "result": result})
			if result.IsError {
				hasError = true
				break
			}
		}
		return outputs, hasError, nil
	}

	if toolName == "" {
		return nil, false, errors.New("call requires a tool name or --calls/--calls-file")
	}
	arguments, err := readArguments(argsJSON, argsFile)
	if err != nil {
		return nil, false, err
	}
	result := svc.callTool(toolName, arguments)
	return result, result.IsError, nil
}

type callSpec struct {
	Tool string
	Args map[string]any
}

func readArguments(inline, file string) (map[string]any, error) {
	if inline != "" && file != "" {
		return nil, errors.New("Use either inline JSON or a JSON file, not both")
	}
	if inline == "" && file == "" {
		return map[string]any{}, nil
	}
	source, err := readJSONSource(inline, file)
	if err != nil {
		return nil, err
	}
	var args map[string]any
	decoder := json.NewDecoder(strings.NewReader(source))
	decoder.UseNumber()
	if err := decoder.Decode(&args); err != nil {
		return nil, fmt.Errorf("Invalid JSON input: %w", err)
	}
	if args == nil {
		return nil, errors.New("--args must be a JSON object")
	}
	return args, nil
}

func readCallSequence(inline, file string) ([]callSpec, error) {
	if inline != "" && file != "" {
		return nil, errors.New("Use either --calls or --calls-file, not both")
	}
	source, err := readJSONSource(inline, file)
	if err != nil {
		return nil, err
	}
	var raw []map[string]any
	decoder := json.NewDecoder(strings.NewReader(source))
	decoder.UseNumber()
	if err := decoder.Decode(&raw); err != nil {
		return nil, fmt.Errorf("Invalid JSON input: %w", err)
	}
	calls := make([]callSpec, 0, len(raw))
	for index, item := range raw {
		name, _ := item["tool"].(string)
		if name == "" {
			name, _ = item["name"].(string)
		}
		if name == "" {
			return nil, fmt.Errorf("call sequence item #%d requires a non-empty tool", index+1)
		}
		args, _ := item["args"].(map[string]any)
		if args == nil {
			args, _ = item["arguments"].(map[string]any)
		}
		if args == nil {
			args = map[string]any{}
		}
		calls = append(calls, callSpec{Tool: name, Args: args})
	}
	return calls, nil
}

func readJSONSource(inline, file string) (string, error) {
	if inline != "" {
		return inline, nil
	}
	if file == "" {
		return "", errors.New("JSON input is required")
	}
	data, err := os.ReadFile(file)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func runMCP(stdin io.Reader, stdout io.Writer) error {
	svc := newService()
	decoder := json.NewDecoder(stdin)
	encoder := json.NewEncoder(stdout)
	for {
		var request map[string]any
		if err := decoder.Decode(&request); err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			_ = encoder.Encode(jsonRPCError(nil, -32700, "Invalid JSON-RPC payload"))
			continue
		}
		response := handleMCPRequest(request, svc)
		if response != nil {
			if err := encoder.Encode(response); err != nil {
				return err
			}
		}
	}
}

func handleMCPRequest(request map[string]any, svc *service) map[string]any {
	id := request["id"]
	method, _ := request["method"].(string)
	params, _ := request["params"].(map[string]any)
	switch method {
	case "initialize":
		return jsonRPCResult(id, map[string]any{
			"protocolVersion": "2025-03-26",
			"serverInfo": map[string]any{
				"name":    "open-computer-use",
				"version": version,
			},
			"capabilities": map[string]any{"tools": map[string]any{"listChanged": false}},
			"instructions": serverInstructions,
		})
	case "notifications/initialized", "notifications/turn-ended":
		return nil
	case "ping":
		return jsonRPCResult(id, map[string]any{})
	case "tools/list":
		return jsonRPCResult(id, map[string]any{"tools": toolDefinitions()})
	case "tools/call":
		name, _ := params["name"].(string)
		arguments, _ := params["arguments"].(map[string]any)
		if arguments == nil {
			arguments = map[string]any{}
		}
		return jsonRPCResult(id, svc.callTool(name, arguments))
	default:
		if method == "" {
			return nil
		}
		return jsonRPCError(id, -32601, "Method not found: "+method)
	}
}

func jsonRPCResult(id any, result any) map[string]any {
	return map[string]any{"jsonrpc": "2.0", "id": id, "result": result}
}

func jsonRPCError(id any, code int, message string) map[string]any {
	return map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"error":   map[string]any{"code": code, "message": message},
	}
}

func helpText(command string) string {
	switch command {
	case "mcp":
		return "Usage:\n  open-computer-use mcp\n\nStart the stdio MCP server.\n"
	case "call":
		return "Usage:\n  open-computer-use call <tool> [--args '<json-object>']\n  open-computer-use call --calls '<json-array>'\n\nThe JSON array form keeps all calls in one process so element_index state can be reused.\n"
	case "snapshot":
		return "Usage:\n  open-computer-use snapshot [--text-limit <positive-int|max>] [--max-tree-nodes <positive-int>] [--max-tree-depth <positive-int>] <app>\n\nPrint the current Linux AT-SPI snapshot for the target app.\n"
	default:
		return `Open Computer Use for Linux

Usage:
  open-computer-use [command] [options]

Commands:
  mcp                  Start the stdio MCP server.
  doctor               Print Linux runtime notes.
  list-apps            Print running apps with top-level windows.
  snapshot <app>       Print the current AT-SPI snapshot for an app.
  call <tool>           Call one tool, or run a JSON array of tool calls.
  help [command]       Show general or command-specific help.
  version              Print the CLI version.

Notes:
  The Linux runtime uses AT-SPI2 semantic actions first, then best-effort
  coordinate/key synthesis. Run it in the signed-in desktop session.
`
	}
}
