package main

import (
	"bytes"
	"context"
	_ "embed"
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
	Index                int      `json:"index"`
	RuntimeID            []int    `json:"runtimeId,omitempty"`
	AutomationID         string   `json:"automationId,omitempty"`
	Name                 string   `json:"name,omitempty"`
	ControlType          string   `json:"controlType,omitempty"`
	LocalizedControlType string   `json:"localizedControlType,omitempty"`
	ClassName            string   `json:"className,omitempty"`
	Value                string   `json:"value,omitempty"`
	NativeWindowHandle   int64    `json:"nativeWindowHandle,omitempty"`
	Frame                *frame   `json:"frame,omitempty"`
	Actions              []string `json:"actions,omitempty"`
	States               string   `json:"states,omitempty"`
	Description          string   `json:"description,omitempty"`
	Placeholder          string   `json:"placeholder,omitempty"`
}

type appSnapshot struct {
	App                 appDescriptor   `json:"app"`
	WindowTitle         string          `json:"windowTitle,omitempty"`
	WindowBounds        *frame          `json:"windowBounds,omitempty"`
	ScreenshotPNGBase64 string          `json:"screenshotPngBase64,omitempty"`
	TreeLines           []string        `json:"treeLines,omitempty"`
	FocusedSummary      string          `json:"focusedSummary,omitempty"`
	SelectedText        string          `json:"selectedText,omitempty"`
	Elements            []elementRecord `json:"elements,omitempty"`
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
	text := s.renderedText()
	if len(notes) > 0 {
		lines := make([]string, 0, len(notes)+1)
		for _, note := range notes {
			lines = append(lines, "Note: "+note)
		}
		lines = append(lines, "", text)
		text = strings.Join(lines, "\n")
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

func (s *service) callTool(name string, args map[string]any) toolCallResult {
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
		return s.getAppState(requiredString(args, "app"), textLimit, maxTreeNodes, maxTreeDepth, optionalBool(args, "prune"))
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

func (s *service) getAppState(app string, textLimit *textLimit, maxTreeNodes, maxTreeDepth *int, prune *bool) toolCallResult {
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
		record, err := lookupElement(snapshot, elementIndex)
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
	record, err := lookupElement(snapshot, elementIndex)
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "invoke_element_action", App: app, Element: record, Action: action})
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
	record, err := lookupElement(snapshot, elementIndex)
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "scroll", App: app, Element: record, Direction: normalized, Pages: pages})
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
	record, err := lookupElement(snapshot, elementIndex)
	if err != nil {
		return textResult(err.Error(), true)
	}
	if mismatch := elementIntentMismatch(record, declared); mismatch != "" {
		return textResult(mismatch, true)
	}
	return s.actionResult(app, linuxRequest{Tool: "set_value", App: app, Element: record, Value: value})
}

// deliveryWasVerified：这次动作在合成之前是否确认过目标窗口处于活动状态。
//
// 只有纯合成类工具才成立。它们走 require_window_focus，夺不到焦点就硬失败，
// 绝不把输入送去别的窗口——所以一旦执行到了这里，就说明输入确实到了这个应用。
// click 不在此列：它可能走的是语义通道，压根没经过焦点确认。
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

func (s *service) actionResult(app string, request linuxRequest) toolCallResult {
	// 动作前的状态在 Go 侧已经缓存着了（动作工具契约要求先调 get_app_state，
	// 且每个动作自己也会刷新缓存），所以 before/after 比对不需要额外遍历一次树。
	before := s.currentSnapshot(app)
	snapshot, notes, result := s.refreshSnapshot(app, request)
	if result.IsError {
		return result
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
			notes = append(notes, "This app's window was verified focused before synthesis, so the input reached this window (which widget inside it received the input is still unverified) — yet nothing observably changed: window title, accessibility tree, focus and selection are identical. Treat this as delivered-but-ignored: repeating the same input will not help; either the input is a no-op here, or the focus sits on a different widget than you assumed.")
		} else {
			notes = append(notes, "Nothing observable changed: the window title, accessibility tree, focus and selection are identical to the state before this action. Treat the action as unconfirmed rather than successful.")
		}
	}
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
	keys := []string{query, snapshot.App.Name, snapshot.App.BundleIdentifier, strconv.Itoa(snapshot.App.PID)}
	for _, key := range keys {
		key = strings.ToLower(strings.TrimSpace(key))
		if key != "" {
			s.snapshots[key] = snapshot
		}
	}
}

func lookupElement(snapshot *appSnapshot, elementIndex string) (*elementRecord, error) {
	index, err := strconv.Atoi(elementIndex)
	if err != nil {
		return nil, fmt.Errorf("unknown element_index %q", elementIndex)
	}
	for _, record := range snapshot.Elements {
		if record.Index == index {
			copy := record
			return &copy, nil
		}
	}
	return nil, fmt.Errorf("unknown element_index %q", elementIndex)
}

// elementIntentMismatch 拿 agent 声明的意图去核对下标解析到的东西。
// 匹配返回空串；明显不匹配则返回一句给 agent 看的解释。
//
// 修的是一类**静默**失败：下标是新取的、没过期，但它来自**上一份**快照。
// 实测（LibreOffice Impress）F4 打开对话框后索引全变，用旧下标调
// click(element_index=5) 时工具照点不误——本想点 Position Y，实际点到菜单，
// 把对象高度误改成 16.26cm，全程没有一条报错。
// `record_still_matches()` 只能拿"存下来的记录"比对，比不了"agent **想**点什么"。
//
// 抄的是 Playwright：它每个动作都带一个 element 参数
// （"Human-readable element description used to obtain permission to interact
// with the element"），本意是可读性与权限，副作用正好是**意图可核对**。
//
// 判据刻意宽松，因为**误拒比漏过更糟**——被误拒的 agent 会以为目标不存在。
// 只在"声明里有实词、却与 role 和 name 都毫无交集"时才拒。
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
	return &response, nil
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

func toolDefinitions() []toolDefinition {
	return []toolDefinition{
		{
			Name:        "click",
			Description: "CHANNEL: ACCESSIBILITY. Click an element addressed by element_index from get_app_state. This invokes the element's own accessibility action when it has one, which does NOT steal focus from whatever the user is doing; when it has none, it synthesizes a click at coordinates taken FROM THE TREE, so the target is still identified rather than guessed. It does not accept x/y \u2014 for a click addressed by pixel, use click_xy. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty("App name or bundle identifier"),
				"element_index": stringProperty("Element index to click, from the most recent get_app_state"),
				"click_count":   integerProperty("Number of clicks. Defaults to 1"),
				"mouse_button":  enumStringProperty("Mouse button to click. Defaults to left.", []string{"left", "right", "middle"}),
				"click_method":  enumStringProperty("Click implementation: auto (default), accessibility, app_post, sky_click, or global. Linux supports global AT-SPI mouse synthesis and does not currently support app_post or sky_click.", clickMethodValues),
			}, []string{"app", "element_index"}),
		},
		{
			Name:        "click_xy",
			Description: "CHANNEL: GUI. Click at a pixel coordinate. This addresses NO element \u2014 whatever happens to be under that point receives the click \u2014 so reach for it only when the target has no element_index in the tree, or when an accessibility action reported success without doing anything. Coordinates are window-relative and are the SAME space as the attached screenshot and as the Frame values in the tree, so a point read off the image can be passed straight in. Always returns a screenshot, and reports which element the hit test found under that point (a hint, not proof). This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":          stringProperty("App name or bundle identifier"),
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
				"app":    stringProperty("App name or bundle identifier"),
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
				"app":            stringProperty("App name or bundle identifier"),
				"text_limit":     textLimitProperty("Maximum text characters to return. Use \"max\" for full text. Defaults to 500."),
				"max_tree_nodes": positiveIntegerProperty("Maximum accessibility tree nodes to render. Defaults to 1200."),
				"max_tree_depth": positiveIntegerProperty("Maximum accessibility tree depth to render. Defaults to 64."),
				"prune":          booleanProperty("Defaults to true. Pruning keeps only interactable, on-screen elements and cuts the tree to roughly a fifth without losing anything you can act on; the omission notice reports how many nodes were left out. Set false only if you suspect a needed element was filtered."),
			}, []string{"app"}),
		},
		{
			Name:        "get_screenshot",
			Description: "CHANNEL: GUI. Take a screenshot of an app's key window WITHOUT the accessibility tree. get_app_state already returns a screenshot next to the tree, so reach for this one only when you want the image alone — re-checking a pixel-level detail, watching a canvas change, or confirming an effect that never reaches the tree — and do not want to pay for the tree again. Costs are not ordered the way you might assume: a window screenshot runs about a thousand tokens, more than a small app's tree (gedit ~350) but LESS than a content-rich one (a file manager tree ~2100). This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app": stringProperty("App name or bundle identifier"),
			}, []string{"app"}),
		},
		{
			Name:        "list_apps",
			Description: "CHANNEL: none — this only enumerates apps. List the apps on this computer. Returns the set of apps that are currently running, as well as any that have been used in the last 14 days, including details on usage frequency. This tool is part of plugin `Computer Use`.",
			Annotations: readOnlyAnnotations(),
			InputSchema: objectSchema(map[string]any{}, nil),
		},
		{
			Name:        "invoke_element_action",
			Description: "CHANNEL: ACCESSIBILITY. Invoke a named accessibility action on an element — a first-class way to drive the UI, not a fallback. `click` already performs each element's default action; this tool reaches the other actions it exposes (e.g. menu, expand, increment). Like click-by-index it does not steal focus, and it is preferred over coordinate clicks whenever the action you need is listed under \"More actions\" in the accessibility tree. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty("App name or bundle identifier"),
				"element_index": stringProperty("Element identifier"),
				"action":        stringProperty("Secondary accessibility action name"),
			}, []string{"app", "element_index", "action"}),
		},
		{
			Name:        "press_key",
			Description: "CHANNEL: KEYBOARD — the key goes to whatever widget currently holds focus inside the window, NOT to any element you name. Press a key or key-combination on the keyboard, including modifier and navigation keys.\n  - This supports xdotool's `key` syntax.\n  - Examples: \"a\", \"Return\", \"Tab\", \"super+c\", \"Up\", \"KP_0\" (for the numpad 0). This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app": stringProperty("App name or bundle identifier"),
				"key": stringProperty("Key or key-combination to press"),
			}, []string{"app", "key"}),
		},
		{
			Name:        "scroll",
			Description: "CHANNEL: KEYBOARD — element_index does NOT target the scroll. Page keys are synthesized to whatever widget holds focus inside the window; if the wrong region scrolled, click that region first. Scroll an element in a direction by a number of pages. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"element":       stringProperty("Human-readable description of the element you intend to act on, e.g. \"the Save button\" or \"Position Y spin button\". Optional but strongly recommended: it is cross-checked against what element_index actually resolves to, which catches the common and otherwise SILENT failure of reusing an index from an earlier snapshot after the indices were renumbered."),
				"app":           stringProperty("App name or bundle identifier"),
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
				"app":           stringProperty("App name or bundle identifier"),
				"element_index": stringProperty("Element identifier"),
				"value":         stringProperty("Value to assign"),
			}, []string{"app", "element_index", "value"}),
		},
		{
			Name:        "type_text",
			Description: "CHANNEL: KEYBOARD (falls back from ACCESSIBILITY) — this first tries the AT-SPI editable-text API on the focused editable control, and only synthesizes keystrokes if that write does not land. Type literal text using keyboard input. This tool is part of plugin `Computer Use`.",
			Annotations: defaultAnnotations(),
			InputSchema: objectSchema(map[string]any{
				"app":  stringProperty("App name or bundle identifier"),
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
