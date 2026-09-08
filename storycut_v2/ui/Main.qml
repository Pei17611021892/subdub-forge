import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    visible: true
    width: 1280
    height: 800
    minimumWidth: 1040
    minimumHeight: 680
    title: "StoryCut v" + appController.appVersion + " · AI 解说剪辑"
    color: "#0b0c10"

    property color accent: "#8b5cf6"
    property color accentLight: "#a78bfa"
    property color panel: "#15171e"
    property color panelRaised: "#1c1f28"
    property color textMain: "#f4f4f5"
    property color textMuted: "#9ca3af"
    property int currentStep: 0
    property int storyTargetDuration: 180
    property bool manuscriptProject: appController.projectType === "manuscript"
    property bool understandingDone: manuscriptProject
                                            ? appController.sourceManuscriptText.trim().length > 0
                                            : appController.events.length > 0
    property bool storyDone: appController.storyNarration.length > 0
                             && (!manuscriptProject || !appController.storyboardStale)
    property bool matchingDone: appController.matches.length > 0
    property bool exportDone: appController.previewVideoReady
    property bool matchingAdvancedVisible: false

    component StepBadge: Rectangle {
        id: stepBadge
        property string stepNumber: "01"
        property bool completed: false
        implicitWidth: 64
        implicitHeight: 54
        radius: 14
        color: completed ? "#183729" : "#342257"
        border.width: 1
        border.color: completed ? "#347456" : "#6d4ca0"

        Column {
            anchors.centerIn: parent
            spacing: 1
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: completed ? "完成" : "STEP"
                color: completed ? "#8ee3b4" : "#bd9cff"
                font.pixelSize: 8
                font.bold: true
                font.letterSpacing: 1
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 4
                Text { text: stepBadge.stepNumber; color: completed ? "#b8f3cf" : "#ffffff"; font.pixelSize: 18; font.bold: true }
                Text { visible: stepBadge.completed; text: "✓"; color: "#8ee3b4"; font.pixelSize: 15; font.bold: true }
            }
        }
    }

    component SubstepBadge: Rectangle {
        id: substepBadge
        property string stepNumber: "1"
        implicitWidth: 32
        implicitHeight: 32
        radius: 10
        color: "#30224d"
        border.color: "#66469b"
        Text {
            anchors.centerIn: parent
            text: substepBadge.stepNumber
            color: "#d4c2ff"
            font.pixelSize: 12
            font.bold: true
        }
    }

    component LoadingRing: Item {
        id: loadingRing
        property color ringColor: accentLight
        property bool running: true
        implicitWidth: 34
        implicitHeight: 34

        Canvas {
            anchors.fill: parent
            antialiasing: true
            onPaint: {
                var ctx = getContext("2d")
                var centerX = width / 2
                var centerY = height / 2
                var radius = Math.max(2, Math.min(width, height) / 2 - 3)
                ctx.clearRect(0, 0, width, height)
                ctx.lineWidth = 3
                ctx.lineCap = "round"
                ctx.strokeStyle = "#34303f"
                ctx.beginPath()
                ctx.arc(centerX, centerY, radius, 0, Math.PI * 2)
                ctx.stroke()
                ctx.strokeStyle = loadingRing.ringColor
                ctx.beginPath()
                ctx.arc(centerX, centerY, radius, -Math.PI / 2, Math.PI * 0.72)
                ctx.stroke()
            }
        }

        RotationAnimator on rotation {
            from: 0
            to: 360
            duration: 850
            loops: Animation.Infinite
            running: loadingRing.running && loadingRing.visible
        }
    }

    component ProcessingDots: Row {
        id: processingDots
        spacing: 2
        Repeater {
            model: 3
            delegate: Item {
                required property int index
                width: 6
                height: 14
                Rectangle {
                    id: movingDot
                    x: 1
                    y: 6
                    width: 4
                    height: 4
                    radius: 2
                    color: accentLight
                    SequentialAnimation on y {
                        running: processingDots.visible
                        loops: Animation.Infinite
                        PauseAnimation { duration: movingDot.parent.index * 120 }
                        NumberAnimation { from: 6; to: 1; duration: 180; easing.type: Easing.OutQuad }
                        NumberAnimation { from: 1; to: 6; duration: 220; easing.type: Easing.InQuad }
                        PauseAnimation { duration: 360 - movingDot.parent.index * 60 }
                    }
                }
            }
        }
    }

    component PreciseSpinBox: Control {
        id: preciseControl
        property int from: 0
        property int to: 100
        property int value: 0
        property int stepSize: 1
        property real divisor: 1
        property string suffix: ""
        signal valueModified()
        Layout.preferredWidth: 72
        Layout.maximumWidth: 72
        implicitWidth: 72
        implicitHeight: 22

        function limited(rawValue) {
            return Math.max(from, Math.min(to, Math.round(rawValue)))
        }

        function formatted(rawValue) {
            return divisor === 1
                    ? String(Math.round(rawValue))
                    : Number(rawValue / divisor).toFixed(1)
        }

        function adjust(delta) {
            value = limited(value + delta * stepSize)
            valueModified()
        }

        background: Rectangle {
            radius: 5
            color: "#20232c"
            border.color: "#3a3f4c"
        }

        contentItem: Row {
            spacing: 0
            Rectangle {
                width: 19; height: 22
                radius: 5
                color: minusArea.pressed ? "#3a334c" : (minusArea.containsMouse ? "#302a3f" : "transparent")
                Text { anchors.centerIn: parent; text: "−"; color: textMuted; font.pixelSize: 11; font.bold: true }
                MouseArea {
                    id: minusArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: preciseControl.adjust(-1)
                }
            }
            TextInput {
                id: preciseInput
                width: 34; height: 22
                text: preciseControl.formatted(preciseControl.value)
                color: accentLight
                font.pixelSize: 9
                horizontalAlignment: TextInput.AlignHCenter
                verticalAlignment: TextInput.AlignVCenter
                selectByMouse: true
                validator: DoubleValidator {}
                onEditingFinished: {
                    var parsed = Number(text)
                    if (!isNaN(parsed)) {
                        preciseControl.value = preciseControl.limited(parsed * preciseControl.divisor)
                        preciseControl.valueModified()
                    }
                    text = preciseControl.formatted(preciseControl.value)
                }
            }
            Rectangle {
                width: 19; height: 22
                radius: 5
                color: plusArea.pressed ? "#3a334c" : (plusArea.containsMouse ? "#302a3f" : "transparent")
                Text { anchors.centerIn: parent; text: "+"; color: textMuted; font.pixelSize: 11; font.bold: true }
                MouseArea {
                    id: plusArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: preciseControl.adjust(1)
                }
            }
        }
    }

    function scrollToSection(item, stepIndex) {
        currentStep = stepIndex
        if (!item)
            return
        mainScroll.contentItem.contentY = Math.max(0, item.y - 18)
    }

    function opaqueRgbaHex(value) {
        function pair(channel) {
            var text = Math.round(Math.max(0, Math.min(1, channel)) * 255).toString(16).toUpperCase()
            return text.length < 2 ? "0" + text : text
        }
        return "#" + pair(value.r) + pair(value.g) + pair(value.b) + "FF"
    }

    font.family: "Microsoft YaHei UI"

    FileDialog {
        id: videoDialog
        title: "选择原始视频"
        nameFilters: ["视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v)", "所有文件 (*)"]
        onAccepted: appController.importVideo(selectedFile.toString())
    }

    FileDialog {
        id: manuscriptFileDialog
        title: "选择中文文稿"
        nameFilters: ["文稿文件 (*.txt *.md *.markdown)", "所有文件 (*)"]
        onAccepted: {
            appController.importManuscript(selectedFile.toString())
            manuscriptCreateDialog.close()
        }
    }

    FileDialog {
        id: manuscriptAssetsDialog
        title: "选择用于匹配分镜的本地素材"
        fileMode: FileDialog.OpenFiles
        nameFilters: [
            "视频和图片 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.jpg *.jpeg *.png *.webp *.bmp)",
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v)",
            "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp)"
        ]
        onAccepted: appController.importManuscriptAssets(selectedFiles)
    }

    Dialog {
        id: stockMediaSettingsDialog
        width: 560
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape

        Timer {
            id: subtitleBackgroundPreviewTimer
            interval: 450
            repeat: false
            onTriggered: {
                if (!subtitleStyleDialog.opened || subtitleStyleDialog.editMode !== "style")
                    return
                if (appController.subtitleEffectPreviewBusy) {
                    restart()
                    return
                }
                appController.generateSubtitleEffectPreview()
            }
        }
        Connections {
            target: appController
            function onSubtitleStyleChanged() {
                if (subtitleStyleDialog.opened
                        && subtitleStyleDialog.editMode === "style"
                        && appController.subtitleStyle.backgroundEnabled
                        && appController.subtitleStyle.backgroundMode !== "mask")
                    subtitleBackgroundPreviewTimer.restart()
            }
        }
        background: Rectangle { radius: 16; color: "#12141b"; border.color: "#3a3f4c" }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "免费素材网站设置"; color: textMain; font.pixelSize: 19; font.bold: true }
            Text {
                Layout.fillWidth: true
                text: "至少配置一个免费 API Key。搜索使用分镜已有关键词，不会增加 OpenAI 请求。"
                color: textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
            Text { text: "Pexels API Key"; color: "#d4d4d8"; font.pixelSize: 11 }
            TextField {
                id: pexelsKeyInput
                Layout.fillWidth: true
                echoMode: stockKeysVisible.checked ? TextInput.Normal : TextInput.Password
                color: textMain; placeholderText: "可留空"
                background: Rectangle { implicitHeight: 40; radius: 9; color: "#1d2028"; border.color: pexelsKeyInput.activeFocus ? accent : "#3a3f4c" }
            }
            Text { text: "Pixabay API Key"; color: "#d4d4d8"; font.pixelSize: 11 }
            TextField {
                id: pixabayKeyInput
                Layout.fillWidth: true
                echoMode: stockKeysVisible.checked ? TextInput.Normal : TextInput.Password
                color: textMain; placeholderText: "可留空"
                background: Rectangle { implicitHeight: 40; radius: 9; color: "#1d2028"; border.color: pixabayKeyInput.activeFocus ? accent : "#3a3f4c" }
            }
            CheckBox {
                id: stockKeysVisible
                text: "显示 API Key"
                contentItem: Text { leftPadding: stockKeysVisible.indicator.width + stockKeysVisible.spacing; text: stockKeysVisible.text; color: textMuted; font.pixelSize: 11 }
            }
            Text {
                Layout.fillWidth: true
                text: "密钥保存在仓库根目录 .env，仅用于调用对应素材站官方搜索接口。"
                color: "#858b98"; font.pixelSize: 10; wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton { text: "取消"; onClicked: stockMediaSettingsDialog.close() }
                FlatButton {
                    text: "保存配置"
                    enabled: pexelsKeyInput.text.trim() !== "" || pixabayKeyInput.text.trim() !== ""
                    onClicked: {
                        if (appController.saveStockMediaConfiguration(pexelsKeyInput.text, pixabayKeyInput.text))
                            stockMediaSettingsDialog.close()
                    }
                }
            }
        }
        onOpened: {
            pexelsKeyInput.text = appController.pexelsApiKey
            pixabayKeyInput.text = appController.pixabayApiKey
            stockKeysVisible.checked = false
        }
        onClosed: {
            pexelsKeyInput.text = ""
            pixabayKeyInput.text = ""
        }
    }

    Dialog {
        id: stockResultsDialog
        width: Math.min(window.width - 70, 920)
        height: Math.min(window.height - 60, 680)
        anchors.centerIn: parent
        modal: true
        padding: 20
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 16; color: "#12141b"; border.color: "#3a3f4c" }
        contentItem: ColumnLayout {
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { text: "免费素材候选"; color: textMain; font.pixelSize: 19; font.bold: true }
                    Text { text: "每个分镜默认选择综合评分最高的结果；下载前可打开来源页面核对。"; color: textMuted; font.pixelSize: 10 }
                }
                GhostButton { text: "关闭"; onClicked: stockResultsDialog.close() }
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: appController.stockSearchResults
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Rectangle {
                    id: stockResultItem
                    required property var modelData
                    property var candidate: modelData.candidates && modelData.candidates.length > 0 ? modelData.candidates[0] : ({})
                    width: ListView.view.width
                    height: 104
                    radius: 10
                    color: "#191c23"
                    border.color: modelData.status === "found" ? "#315d46" : "#70404a"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 9; spacing: 11
                        Rectangle {
                            Layout.preferredWidth: 132; Layout.fillHeight: true
                            radius: 7; color: "#0b0c10"; clip: true
                            Image { anchors.fill: parent; source: stockResultItem.candidate.preview_url || ""; fillMode: Image.PreserveAspectCrop; asynchronous: true }
                            Text { anchors.centerIn: parent; visible: !stockResultItem.candidate.preview_url; text: "未找到素材"; color: textMuted; font.pixelSize: 10 }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 4
                            Text { text: "分镜 " + modelData.narration_id + " · " + modelData.ideal_shot_zh; color: textMain; font.pixelSize: 11; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                            Text { text: "搜索词：" + modelData.query; color: textMuted; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                            Text {
                                text: stockResultItem.candidate.provider
                                      ? stockResultItem.candidate.provider + " · " + (stockResultItem.candidate.author || "未知作者") + " · " + (stockResultItem.candidate.duration_sec || 0) + " 秒 · " + (stockResultItem.candidate.width || 0) + "×" + (stockResultItem.candidate.height || 0)
                                      : "没有匹配结果，后续可导入本地素材补充"
                                color: stockResultItem.candidate.provider ? "#8ee3b4" : "#ff9ba6"; font.pixelSize: 10
                            }
                            Text { visible: modelData.local_path; text: "✓ 已下载到项目素材目录"; color: "#8ee3b4"; font.pixelSize: 10 }
                        }
                        GhostButton {
                            visible: stockResultItem.candidate.page_url
                            text: "查看来源"
                            onClicked: appController.openWebUrl(stockResultItem.candidate.page_url)
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: "素材来自 Pexels / Pixabay；请保留项目记录中的作者和来源链接。"; color: "#858b98"; font.pixelSize: 9; Layout.fillWidth: true }
                FlatButton {
                    text: appController.stockSearchBusy ? "正在下载…" : "下载自动选择的素材"
                    enabled: appController.stockSearchResults.length > 0 && !appController.stockSearchBusy
                    onClicked: appController.downloadStockSelections()
                }
            }
        }
    }

    Dialog {
        id: projectTypeDialog
        width: Math.min(window.width - 80, 760)
        anchors.centerIn: parent
        modal: true
        padding: 24
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 18
            color: "#12141b"
            border.color: "#393346"
        }

        contentItem: ColumnLayout {
            spacing: 18
            Text { text: "创建 StoryCut 项目"; color: textMain; font.pixelSize: 22; font.bold: true }
            Text {
                Layout.fillWidth: true
                text: "选择你的起始素材。两种模式使用相同的配音、字幕、检查与导出流程。"
                color: textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Repeater {
                    model: [
                        {
                            icon: "▶",
                            title: "从视频开始",
                            detail: "导入已有长视频，理解语音和画面，再压缩为三分钟内的英文解说。",
                            action: "video"
                        },
                        {
                            icon: "文",
                            title: "从文稿开始",
                            detail: "粘贴或导入纯文稿，先规划所需分镜，再准备素材并自动匹配。",
                            action: "manuscript"
                        }
                    ]
                    delegate: Rectangle {
                        id: projectModeCard
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 178
                        radius: 15
                        color: projectModeMouse.containsMouse ? "#201b2b" : panel
                        border.width: 1
                        border.color: projectModeMouse.containsMouse ? accent : "#30333d"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 9
                            Rectangle {
                                Layout.preferredWidth: 44
                                Layout.preferredHeight: 44
                                radius: 12
                                color: "#34234f"
                                Text { anchors.centerIn: parent; text: projectModeCard.modelData.icon; color: accentLight; font.pixelSize: 19; font.bold: true }
                            }
                            Text { text: projectModeCard.modelData.title; color: textMain; font.pixelSize: 17; font.bold: true }
                            Text {
                                Layout.fillWidth: true
                                text: projectModeCard.modelData.detail
                                color: textMuted
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                            }
                            Item { Layout.fillHeight: true }
                            Text { text: "开始  →"; color: accentLight; font.pixelSize: 12; font.bold: true }
                        }
                        MouseArea {
                            id: projectModeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                projectTypeDialog.close()
                                if (projectModeCard.modelData.action === "video")
                                    videoDialog.open()
                                else
                                    manuscriptCreateDialog.open()
                            }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: manuscriptCreateDialog
        width: Math.min(window.width - 80, 820)
        height: Math.min(window.height - 70, 650)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        onOpened: manuscriptDraft.text = ""

        background: Rectangle {
            radius: 18
            color: "#12141b"
            border.color: "#44365b"
        }

        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Text { text: "从文稿开始"; color: textMain; font.pixelSize: 22; font.bold: true }
                    Text { text: "粘贴完整中文文稿，或导入 UTF-8、UTF-16、GB18030 编码的 TXT / Markdown。"; color: textMuted; font.pixelSize: 11 }
                }
                GhostButton { text: "选择文稿文件"; onClicked: manuscriptFileDialog.open() }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 13
                color: "#0d0f14"
                border.color: manuscriptDraft.activeFocus ? accent : "#30333d"
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 3
                    TextArea {
                        id: manuscriptDraft
                        placeholderText: "在这里粘贴文稿，例如：大象之间是否拥有真正的语言？……"
                        color: textMain
                        placeholderTextColor: "#616673"
                        font.pixelSize: 14
                        wrapMode: TextEdit.Wrap
                        selectByMouse: true
                        padding: 16
                        background: null
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: manuscriptDraft.text.trim().length + " 字符"; color: textMuted; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                GhostButton { text: "取消"; onClicked: manuscriptCreateDialog.close() }
                FlatButton {
                    text: "创建文稿项目  →"
                    enabled: manuscriptDraft.text.trim().length > 0
                    onClicked: {
                        appController.createManuscriptProject(manuscriptDraft.text)
                        manuscriptCreateDialog.close()
                        scrollToSection(understandingSection, 1)
                    }
                }
            }
        }
    }

    Timer {
        id: manuscriptAutosaveTimer
        interval: 700
        repeat: false
        onTriggered: {
            if (manuscriptProject && sourceManuscriptArea.text.trim().length > 0)
                appController.updateSourceManuscript(sourceManuscriptArea.text)
        }
    }

    Connections {
        target: appController
        function onProjectChanged() {
            if (!sourceManuscriptArea.activeFocus
                    && sourceManuscriptArea.text !== appController.sourceManuscriptText)
                sourceManuscriptArea.text = appController.sourceManuscriptText
        }
    }

    FileDialog {
        id: relinkVideoDialog
        title: "重新选择当前项目的原视频"
        nameFilters: ["视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v)", "所有文件 (*)"]
        onAccepted: appController.relinkSourceVideo(selectedFile.toString())
    }

    Dialog {
        id: previewDialog
        width: Math.min(window.width - 100, 980)
        height: Math.min(window.height - 80, 680)
        anchors.centerIn: parent
        modal: true
        closePolicy: Popup.CloseOnEscape
        padding: 0

        background: Rectangle {
            radius: 18
            color: "#111319"
            border.color: "#343843"
        }

        contentItem: ColumnLayout {
            spacing: 14
            anchors.margins: 22

            RowLayout {
                Layout.fillWidth: true
                Text { text: appController.projectName; color: textMain; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                Text { text: appController.previewPositionText + " / " + appController.durationText; color: textMuted; font.pixelSize: 13 }
                GhostButton { text: "关闭"; onClicked: previewDialog.close() }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 13
                color: "#08090c"
                clip: true
                Image {
                    anchors.fill: parent
                    source: appController.previewUrl
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                }
                Rectangle {
                    anchors.centerIn: parent
                    width: 150; height: 42; radius: 10
                    color: "#cc171920"
                    visible: appController.previewBusy
                    Text { anchors.centerIn: parent; text: "正在定位画面…"; color: textMain; font.pixelSize: 13 }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                GhostButton {
                    text: "− 5 秒"
                    onClicked: {
                        previewSlider.value = Math.max(0, previewSlider.value - 5)
                        appController.requestPreviewFrame(previewSlider.value)
                    }
                }
                Slider {
                    id: previewSlider
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, appController.durationSeconds)
                    value: appController.previewPosition
                    onMoved: previewSeekTimer.restart()
                    background: Rectangle {
                        x: previewSlider.leftPadding
                        y: previewSlider.topPadding + previewSlider.availableHeight / 2 - height / 2
                        width: previewSlider.availableWidth
                        height: 5
                        radius: 3
                        color: "#343843"
                        Rectangle { width: previewSlider.visualPosition * parent.width; height: parent.height; radius: 3; color: accent }
                    }
                    handle: Rectangle {
                        x: previewSlider.leftPadding + previewSlider.visualPosition * (previewSlider.availableWidth - width)
                        y: previewSlider.topPadding + previewSlider.availableHeight / 2 - height / 2
                        width: 18; height: 18; radius: 9
                        color: previewSlider.pressed ? accentLight : "#ede9fe"
                    }
                }
                GhostButton {
                    text: "+ 5 秒"
                    onClicked: {
                        previewSlider.value = Math.min(previewSlider.to, previewSlider.value + 5)
                        appController.requestPreviewFrame(previewSlider.value)
                    }
                }
            }
        }

        onOpened: {
            previewSlider.value = appController.previewPosition
            appController.requestPreviewFrame(previewSlider.value)
        }
    }

    Timer {
        id: previewSeekTimer
        interval: 180
        repeat: false
        onTriggered: appController.requestPreviewFrame(previewSlider.value)
    }

    FileDialog {
        id: projectDialog
        title: "打开其他 StoryCut 项目文件"
        nameFilters: ["StoryCut 项目 (project.json)"]
        onAccepted: {
            recentProjectsDialog.openProjectWithLoading(selectedFile.toString())
        }
    }

    Dialog {
        id: recentProjectsDialog
        objectName: "recentProjectsDialog"
        property bool openingProject: false
        property string openingProjectUrl: ""
        function openProjectWithLoading(projectUrl) {
            if (openingProject || !projectUrl)
                return
            openingProject = true
            openingProjectUrl = projectUrl
            openProjectTimer.restart()
        }
        width: Math.min(window.width - 80, 780)
        height: Math.min(
                    window.height - 70,
                    Math.max(430, 260 + Math.min(appController.recentProjects.length, 4) * 102)
                )
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: openingProject ? Popup.NoAutoClose : Popup.CloseOnEscape

        Timer {
            id: openProjectTimer
            interval: 60
            repeat: false
            onTriggered: {
                try {
                    appController.openProject(recentProjectsDialog.openingProjectUrl)
                } finally {
                    recentProjectsDialog.openingProject = false
                    recentProjectsDialog.openingProjectUrl = ""
                    recentProjectsDialog.close()
                }
            }
        }

        background: Rectangle {
            radius: 18
            color: "#111319"
            border.color: "#343843"
        }

        contentItem: ColumnLayout {
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 78
                Layout.leftMargin: 24
                Layout.rightMargin: 20
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text { text: "最近项目"; color: textMain; font.pixelSize: 21; font.bold: true }
                    Text {
                        text: appController.recentProjects.length > 0
                              ? "选择一个项目继续上次的制作"
                              : "还没有可继续的 StoryCut 项目"
                        color: textMuted
                        font.pixelSize: 12
                    }
                }
                GhostButton { text: "关闭"; enabled: !recentProjectsDialog.openingProject; onClicked: recentProjectsDialog.close() }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#292c35" }

            ListView {
                id: recentProjectsList
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 18
                spacing: 10
                clip: true
                model: appController.recentProjects

                Text {
                    anchors.centerIn: parent
                    visible: recentProjectsList.count === 0
                    text: "创建项目后，会在这里显示制作进度"
                    color: "#686d78"
                    font.pixelSize: 12
                }

                delegate: Rectangle {
                    required property var modelData
                    width: recentProjectsList.width
                    height: 92
                    radius: 13
                    color: projectRowMouse.containsMouse ? panelRaised : panel
                    border.color: projectRowMouse.containsMouse ? "#51436d" : "#292c36"

                    MouseArea {
                        id: projectRowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: !recentProjectsDialog.openingProject
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            recentProjectsDialog.openProjectWithLoading(modelData.projectFile)
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 13

                        Rectangle {
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            radius: 11
                            color: "#292238"
                            Text { anchors.centerIn: parent; text: "▶"; color: accentLight; font.pixelSize: 17 }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                Layout.fillWidth: true
                                text: modelData.name
                                color: textMain
                                font.pixelSize: 14
                                font.bold: true
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: modelData.video
                                color: textMuted
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                            }
                            Text {
                                text: (modelData.seriesText ? modelData.seriesText + "  ·  " : "")
                                      + modelData.stageText
                                      + (modelData.updatedText ? "  ·  " + modelData.updatedText : "")
                                color: "#8b91a0"
                                font.pixelSize: 10
                            }
                        }

                        Button {
                            id: continueProjectButton
                            implicitWidth: 112
                            implicitHeight: 40
                            enabled: !recentProjectsDialog.openingProject
                            contentItem: Item {
                                LoadingRing {
                                    width: 17
                                    height: 17
                                    anchors.left: parent.left
                                    anchors.leftMargin: 7
                                    anchors.verticalCenter: parent.verticalCenter
                                    running: recentProjectsDialog.openingProjectUrl === modelData.projectFile
                                    visible: running
                                }
                                Text {
                                    anchors.centerIn: parent
                                    text: recentProjectsDialog.openingProjectUrl === modelData.projectFile ? "加载中…" : "继续"
                                    color: recentProjectsDialog.openingProjectUrl === modelData.projectFile ? "#ede9fe" : continueProjectButton.enabled ? "#d4d4d8" : "#8b91a0"
                                    font.pixelSize: 12
                                }
                            }
                            background: Rectangle {
                                radius: 9
                                color: continueProjectButton.hovered ? "#292d38" : "transparent"
                                border.color: "#343843"
                            }
                            onClicked: {
                                recentProjectsDialog.openProjectWithLoading(modelData.projectFile)
                            }
                        }

                        Button {
                            id: deleteRecentButton
                            text: "删除"
                            implicitWidth: 58
                            implicitHeight: 36
                            enabled: !recentProjectsDialog.openingProject
                            contentItem: Text {
                                text: deleteRecentButton.text
                                color: deleteRecentButton.hovered ? "#fecaca" : "#fca5a5"
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 9
                                color: deleteRecentButton.hovered ? "#402126" : "transparent"
                                border.color: "#633039"
                            }
                            onClicked: {
                                deleteProjectDialog.projectName = modelData.name
                                deleteProjectDialog.projectFile = modelData.projectFile
                                deleteProjectDialog.seriesText = modelData.seriesText || ""
                                deleteProjectDialog.open()
                            }
                        }
                    }
                }

                ScrollBar.vertical: ScrollBar {}
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 24
                Layout.rightMargin: 24
                Layout.bottomMargin: 20
                spacing: 10

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    radius: 10
                    color: "#171923"
                    border.color: "#2d3140"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 13
                        spacing: 10
                        Text { text: "提示"; color: accentLight; font.pixelSize: 12; font.bold: true }
                        Text {
                            Layout.fillWidth: true
                            text: "项目会自动保存。删除项目将清理分析、配音、预览等内容，不会删除项目目录外的原始视频。"
                            color: textMuted
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "项目不在列表中？可手动选择该项目目录内的 project.json。"
                        color: "#747986"
                        font.pixelSize: 11
                    }
                    GhostButton { text: "打开其他项目文件"; onClicked: projectDialog.open() }
                }
            }
        }
    }

    Dialog {
        id: deleteProjectDialog
        property string projectName: ""
        property string projectFile: ""
        property string seriesText: ""
        width: 470
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#563039"
        }

        contentItem: ColumnLayout {
            spacing: 13
            Text { text: "确认删除项目？"; color: textMain; font.pixelSize: 19; font.bold: true }
            Text {
                Layout.fillWidth: true
                text: "将永久删除“" + deleteProjectDialog.projectName + "”"
                      + (deleteProjectDialog.seriesText !== "" ? "的全部 " + deleteProjectDialog.seriesText : "")
                      + "项目文件、分析结果、配音、预览和导出缓存。"
                color: textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                text: "项目目录外的原始视频不会被删除。此操作无法撤销。"
                color: "#fca5a5"
                font.pixelSize: 12
                font.bold: true
                wrapMode: Text.WordWrap
            }
        }

        footer: Item {
            implicitHeight: 68
            Row {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: 22
                anchors.bottomMargin: 18
                spacing: 10
                GhostButton { text: "取消"; onClicked: deleteProjectDialog.close() }
                Button {
                    id: confirmDeleteButton
                    text: "确认删除"
                    implicitHeight: 40
                    leftPadding: 17
                    rightPadding: 17
                    contentItem: Text {
                        text: confirmDeleteButton.text
                        color: "#ffffff"
                        font.pixelSize: 13
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 9
                        color: confirmDeleteButton.down ? "#991b1b" : confirmDeleteButton.hovered ? "#dc2626" : "#b91c1c"
                    }
                    onClicked: {
                        appController.deleteProject(deleteProjectDialog.projectFile)
                        deleteProjectDialog.close()
                    }
                }
            }
        }
    }

    FileDialog {
        id: narrationAudioDialog
        title: "选择 GPT-SoVITS 生成的英文配音"
        nameFilters: ["音频文件 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)", "所有文件 (*)"]
        onAccepted: appController.importNarrationAudio(selectedFile.toString())
    }

    FileDialog {
        id: narrationSrtDialog
        title: "选择 GPT-SoVITS 导出的同步英文 SRT"
        nameFilters: ["SRT 字幕 (*.srt)", "所有文件 (*)"]
        onAccepted: appController.importNarrationSrt(selectedFile.toString())
    }

    ColorDialog {
        id: subtitleTextColorDialog
        title: "选择字幕文字颜色"
        onAccepted: appController.updateSubtitleStyle("textColor", window.opaqueRgbaHex(selectedColor))
    }

    ColorDialog {
        id: subtitleOutlineColorDialog
        title: "选择字幕描边颜色"
        onAccepted: appController.updateSubtitleStyle("outlineColor", window.opaqueRgbaHex(selectedColor))
    }

    FileDialog {
        id: saveTtsSrtDialog
        property string exportFormat: "srt"
        title: exportFormat === "txt" ? "导出配音纯文本 · 选择保存位置" : "导出 GPT-SoVITS SRT · 选择保存位置"
        fileMode: FileDialog.SaveFile
        defaultSuffix: exportFormat
        nameFilters: exportFormat === "txt" ? ["纯文本 (*.txt)"] : ["SRT 字幕 (*.srt)"]
        onAccepted: appController.saveTtsSrt(selectedFile.toString(), exportFormat)
    }

    FileDialog {
        id: saveFinalVideoDialog
        title: "导出最终成片 · 选择保存位置"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "mp4"
        nameFilters: ["MP4 视频 (*.mp4)"]
        onAccepted: appController.saveFinalVideo(selectedFile.toString())
    }

    Dialog {
        id: deleteSubtitleCleanedVideoDialog
        width: 470
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 16; color: "#14161d"; border.color: "#53323a" }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "删除去字幕中间视频？"; color: textMain; font.pixelSize: 18; font.bold: true }
            Text {
                Layout.fillWidth: true
                text: "将删除当前项目内生成的 source_without_subtitles.mp4。之后字幕样式预览和成片合成会重新使用原视频。"
                color: textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
            Text { text: "此操作无法撤销，但可以重新设置遮罩并再次生成。"; color: "#fca5a5"; font.pixelSize: 10 }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton { text: "取消"; onClicked: deleteSubtitleCleanedVideoDialog.close() }
                Button {
                    id: confirmDeleteCleanedVideoButton
                    text: "确认删除"
                    implicitHeight: 40
                    leftPadding: 17; rightPadding: 17
                    contentItem: Text { text: confirmDeleteCleanedVideoButton.text; color: "white"; font.pixelSize: 12; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { radius: 9; color: confirmDeleteCleanedVideoButton.down ? "#991b1b" : confirmDeleteCleanedVideoButton.hovered ? "#dc2626" : "#b91c1c" }
                    onClicked: {
                        appController.deleteSubtitleCleanedVideo()
                        deleteSubtitleCleanedVideoDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: durationRevisionDialog
        width: Math.min(window.width - 70, 980)
        height: Math.min(window.height - 60, 720)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: appController.durationRevisionBusy ? Popup.NoAutoClose : Popup.CloseOnEscape

        background: Rectangle {
            radius: 18
            color: "#12141b"
            border.color: "#4b3a68"
        }

        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44
                    radius: 13
                    color: "#302348"
                    Text { anchors.centerIn: parent; text: "✂"; color: accentLight; font.pixelSize: 20; font.bold: true }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text { text: "真实配音超时 · AI 精简方案"; color: textMain; font.pixelSize: 20; font.bold: true }
                    Text {
                        Layout.fillWidth: true
                        text: appController.durationRevisionStatus
                        color: textMuted
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                    }
                }
                GhostButton {
                    text: "关闭"
                    enabled: !appController.durationRevisionBusy
                    onClicked: durationRevisionDialog.close()
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2b2f39" }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: appController.durationRevisionBusy
                LoadingRing {
                    width: 54
                    height: 54
                    anchors.centerIn: parent
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.verticalCenter
                    anchors.topMargin: 42
                    text: "正在保留主线、精彩镜头和完整结尾…"
                    color: textMuted
                    font.pixelSize: 11
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !appController.durationRevisionBusy && appController.durationRevisionReady
                spacing: 12
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    radius: 10
                    color: "#1a1d25"
                    border.color: "#303541"
                    Text {
                        anchors.fill: parent
                        anchors.margins: 12
                        text: appController.durationRevisionSummary
                        color: "#d6d8df"
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 12
                    Repeater {
                        model: [
                            { title: "当前故事", stats: appController.durationRevisionBeforeStats, body: appController.durationRevisionBeforeText, tone: "#f1b86b" },
                            { title: "精简后", stats: appController.durationRevisionAfterStats, body: appController.durationRevisionAfterText, tone: "#8ee3b4" }
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 12
                            color: "#171920"
                            border.color: "#303541"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: modelData.title; color: modelData.tone; font.pixelSize: 14; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Text { text: modelData.stats; color: textMuted; font.pixelSize: 10 }
                                }
                                ScrollView {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    clip: true
                                    TextArea {
                                        text: modelData.body
                                        readOnly: true
                                        selectByMouse: true
                                        wrapMode: TextEdit.Wrap
                                        color: "#d9dce4"
                                        font.pixelSize: 11
                                        background: Rectangle { color: "#101116"; radius: 8 }
                                    }
                                }
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: appController.durationRevisionChanges.length > 0
                    text: "主要调整：" + appController.durationRevisionChanges.join("；")
                    color: textMuted
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "应用后会自动重匹配镜头；应用前的故事、镜头、配音、SRT 和项目设置会完整归档，可一键恢复。"
                        color: "#aeb4c2"
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }
                    GhostButton { text: "暂不应用"; onClicked: durationRevisionDialog.close() }
                    FlatButton {
                        text: "应用新稿并重新匹配"
                        onClicked: {
                            appController.applyNarrationDurationRevision()
                            durationRevisionDialog.close()
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: restoreRevisionDialog
        objectName: "restoreRevisionDialog"
        width: Math.min(window.width - 70, 980)
        height: Math.min(window.height - 60, 700)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        background: Rectangle {
            radius: 16
            color: "#14161d"
            border.color: "#4b4260"
        }
        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44
                    radius: 13
                    color: "#302348"
                    Text { anchors.centerIn: parent; text: "↶"; color: accentLight; font.pixelSize: 22; font.bold: true }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text { text: "版本对比与恢复"; color: textMain; font.pixelSize: 20; font.bold: true }
                    Text {
                        Layout.fillWidth: true
                        text: "右侧是即将恢复的版本。确认前不会修改当前项目。"
                        color: textMuted
                        font.pixelSize: 11
                    }
                }
                GhostButton { text: "关闭"; onClicked: restoreRevisionDialog.close() }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2b2f39" }
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12
                Repeater {
                    model: [
                        {
                            title: "当前版本",
                            stats: appController.durationRevisionRestoreCurrentStats,
                            body: appController.durationRevisionRestoreCurrentText,
                            tone: "#8ee3b4",
                            badge: "正在使用"
                        },
                        {
                            title: "将恢复的版本",
                            stats: appController.durationRevisionRestoreArchivedStats,
                            body: appController.durationRevisionRestoreArchivedText,
                            tone: "#f1b86b",
                            badge: "归档版本"
                        }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 12
                        color: "#171920"
                        border.color: "#303541"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: modelData.title; color: modelData.tone; font.pixelSize: 14; font.bold: true }
                                Rectangle {
                                    Layout.preferredWidth: 58
                                    Layout.preferredHeight: 22
                                    radius: 7
                                    color: "#242731"
                                    Text { anchors.centerIn: parent; text: modelData.badge; color: modelData.tone; font.pixelSize: 9 }
                                }
                                Item { Layout.fillWidth: true }
                                Text { text: modelData.stats; color: textMuted; font.pixelSize: 10 }
                            }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                TextArea {
                                    text: modelData.body
                                    readOnly: true
                                    selectByMouse: true
                                    wrapMode: TextEdit.Wrap
                                    color: "#d9dce4"
                                    font.pixelSize: 11
                                    background: Rectangle { color: "#101116"; radius: 8 }
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                radius: 10
                color: "#1a1d25"
                border.color: "#303541"
                Text {
                    anchors.fill: parent
                    anchors.margins: 11
                    text: "恢复范围：故事、镜头匹配、粗剪时间线、英文配音、同步 SRT 和项目设置。当前版本会先另行归档，恢复后仍可撤回。  " + appController.durationRevisionArchiveText
                    color: "#aeb4c2"
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                    verticalAlignment: Text.AlignVCenter
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton { text: "保留当前版本"; onClicked: restoreRevisionDialog.close() }
                FlatButton {
                    text: "确认恢复右侧版本"
                    onClicked: {
                        restoreRevisionDialog.close()
                        appController.restoreDurationRevisionArchive()
                    }
                }
            }
        }
    }

    Dialog {
        id: qualityCheckDialog
        width: Math.min(window.width - 80, 680)
        height: Math.min(window.height - 70, 590)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 18
            color: "#12141b"
            border.color: appController.qualityCheckBusy ? "#4b4260" : appController.qualityCheckPassed ? "#326b4d" : "#6b3b3b"
        }

        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 16
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44
                    radius: 13
                    color: appController.qualityCheckBusy ? "#292238" : appController.qualityCheckPassed ? "#173526" : "#3b2528"
                    Text {
                        anchors.centerIn: parent
                        text: appController.qualityCheckBusy ? "…" : appController.qualityCheckPassed ? "✓" : "×"
                        color: appController.qualityCheckBusy ? accentLight : appController.qualityCheckPassed ? "#8ee3b4" : "#fca5a5"
                        font.pixelSize: 20
                        font.bold: true
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text { text: "成片质量检查"; color: textMain; font.pixelSize: 20; font.bold: true }
                    Text {
                        text: appController.qualityCheckBusy
                              ? "正在读取项目文件、时间线、字幕与成片参数…"
                              : appController.qualityCheckPassed
                              ? "检查通过；提醒项不会阻止导出。"
                              : "请先处理标记为“×”的问题，再生成成片。"
                        color: textMuted
                        font.pixelSize: 11
                    }
                }
                GhostButton { text: "关闭"; onClicked: qualityCheckDialog.close() }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2b2f39" }

            RowLayout {
                Layout.fillWidth: true
                visible: !appController.qualityCheckBusy
                spacing: 8
                Repeater {
                    model: [
                        { label: "通过", count: appController.qualityPassCount, color: "#8ee3b4", background: "#173526" },
                        { label: "说明", count: appController.qualityInfoCount, color: "#93c5fd", background: "#172b45" },
                        { label: "提醒", count: appController.qualityWarningCount, color: "#fcd34d", background: "#3a3117" },
                        { label: "必须处理", count: appController.qualityErrorCount, color: "#fca5a5", background: "#3b2528" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        Layout.preferredWidth: 105
                        Layout.preferredHeight: 30
                        radius: 8
                        color: modelData.background
                        Text { anchors.centerIn: parent; text: modelData.label + "  " + modelData.count; color: modelData.color; font.pixelSize: 10; font.bold: true }
                    }
                }
                Item { Layout.fillWidth: true }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: appController.qualityCheckBusy
                LoadingRing {
                    id: qualityLoadingRing
                    width: 52
                    height: 52
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: -38
                    running: appController.qualityCheckBusy
                }
                Text {
                    anchors.top: qualityLoadingRing.bottom
                    anchors.topMargin: 16
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "正在执行成片检查…"
                    color: textMain
                    font.pixelSize: 14
                    font.bold: true
                }
                Text {
                    anchors.top: qualityLoadingRing.bottom
                    anchors.topMargin: 44
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "已有成片时会在后台调用 FFprobe，通常只需几秒。"
                    color: textMuted
                    font.pixelSize: 10
                }
            }

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !appController.qualityCheckBusy
                clip: true
                spacing: 8
                model: appController.qualityCheckItems
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width
                    height: 76
                    radius: 10
                    property color levelColor: modelData.level === "pass" ? "#63d39a"
                                               : modelData.level === "error" ? "#f87171"
                                               : modelData.level === "warning" ? "#fbbf24"
                                               : "#60a5fa"
                    color: modelData.level === "pass" ? "#13271e"
                           : modelData.level === "error" ? "#2b181d"
                           : modelData.level === "warning" ? "#2b2515"
                           : "#142338"
                    border.color: levelColor
                    border.width: 1
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 11
                        spacing: 11
                        Rectangle {
                            Layout.preferredWidth: 30
                            Layout.preferredHeight: 30
                            radius: 15
                            color: modelData.level === "pass" ? "#1d4a34"
                                   : modelData.level === "error" ? "#5a252d"
                                   : modelData.level === "warning" ? "#5a4717"
                                   : "#1d3d63"
                            Text {
                                anchors.centerIn: parent
                                text: modelData.level === "pass" ? "✓" : modelData.level === "error" ? "×" : modelData.level === "warning" ? "!" : "i"
                                color: levelColor
                                font.pixelSize: 15
                                font.bold: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text { text: modelData.title; color: levelColor; font.pixelSize: 12; font.bold: true }
                            Text { text: modelData.detail; color: "#c5c9d2"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: apiPreflightDialog
        property string requestedAction: "understanding"
        width: 520
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#3a3f4c"
        }

        contentItem: ColumnLayout {
            spacing: 14
            Text {
                text: apiPreflightDialog.requestedAction === "understanding"
                      ? "开始前缺少 AI 接口配置"
                      : "无法生成故事"
                color: textMain
                font.pixelSize: 19
                font.bold: true
            }
            Text {
                Layout.fillWidth: true
                text: apiPreflightDialog.requestedAction === "understanding"
                      ? "没有检测到 API Key。建议先设置后再开始，这样才能完整理解画面并继续组织故事。"
                      : "第 2 步必须调用 AI 生成故事和英文解说，请先设置 API Key。"
                color: textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: apiPreflightDialog.requestedAction === "understanding" ? 74 : 48
                radius: 9
                color: "#1d2028"
                border.color: "#303541"
                Text {
                    anchors.fill: parent
                    anchors.margins: 12
                    text: apiPreflightDialog.requestedAction === "understanding"
                          ? "“仅做本地分析”用于提前完成语音转写、场景切分和关键帧提取等耗时预处理。它不会生成视觉描述，第 2 步仍然需要 API。"
                          : appController.apiConfigurationHint
                    color: "#c6cad4"
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    verticalAlignment: Text.AlignVCenter
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton {
                    visible: apiPreflightDialog.requestedAction === "understanding"
                    text: "仅做本地预处理"
                    onClicked: {
                        apiPreflightDialog.close()
                        appController.startUnderstandingLocalOnly()
                    }
                }
                GhostButton {
                    text: "取消"
                    onClicked: apiPreflightDialog.close()
                }
                FlatButton {
                    id: setApiKeyButton
                    text: "设置 API Key"
                    onClicked: {
                        apiPreflightDialog.close()
                        apiKeySetupDialog.requestedAction = apiPreflightDialog.requestedAction
                        apiKeySetupDialog.open()
                    }
                }
            }
        }

        onOpened: setApiKeyButton.forceActiveFocus()
    }

    Dialog {
        id: apiKeySetupDialog
        objectName: "apiKeySetupDialog"
        property string requestedAction: "settings"
        width: 600
        height: Math.min(window.height - 50, 740)
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape

        function saveAndContinue() {
            if (!appController.saveApiConfiguration(
                    apiKeyInput.text,
                    apiBaseUrlInput.text,
                    storyModelInput.text,
                    visionModelInput.text,
                    editorModelInput.text,
                    reasoningValue(storyReasoningCombo.currentIndex),
                    reasoningValue(editorReasoningCombo.currentIndex)))
                return
            close()
            if (requestedAction === "understanding")
                appController.startUnderstanding()
            else if (requestedAction === "story")
                appController.generateStory(storyTargetDuration)
        }

        function reasoningValue(index) {
            if (index === 1) return "low"
            if (index === 2) return "medium"
            if (index === 3) return "high"
            if (index === 4) return "xhigh"
            return ""
        }

        function reasoningIndex(value) {
            if (value === "low") return 1
            if (value === "medium") return 2
            if (value === "high") return 3
            if (value === "xhigh") return 4
            return 0
        }

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#3a3f4c"
        }

        contentItem: ScrollView {
            clip: true
            contentWidth: availableWidth
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {
            width: parent.width
            spacing: 12
            Text {
                text: apiKeySetupDialog.requestedAction === "settings" ? "API 设置" : "快速设置 API"
                color: textMain
                font.pixelSize: 19
                font.bold: true
            }
            Text {
                Layout.fillWidth: true
                text: "在这里直接管理 API Key 和接口地址。保存后立即生效，无需寻找或手动编辑 .env。"
                color: textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                radius: 9
                color: appController.apiConfigured ? "#172b22" : "#2a2023"
                border.color: appController.apiConfigured ? "#326b4d" : "#604047"
                Column {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 4
                    Text {
                        text: appController.apiConfigured ? "● 当前已配置  " + appController.apiKeyMasked : "● 当前未配置 API Key"
                        color: appController.apiConfigured ? "#8ee3b4" : "#f0a6b2"
                        font.pixelSize: 11
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        text: appController.apiBaseUrl || "OpenAI 官方接口（未填写自定义地址）"
                        color: "#aeb4c0"
                        font.pixelSize: 10
                        elide: Text.ElideMiddle
                    }
                }
            }
            Text { text: "API Key"; color: "#d4d4d8"; font.pixelSize: 12 }
            TextField {
                id: apiKeyInput
                Layout.fillWidth: true
                placeholderText: "粘贴 API Key"
                echoMode: showApiKey.checked ? TextInput.Normal : TextInput.Password
                selectByMouse: true
                color: textMain
                placeholderTextColor: "#71717a"
                onAccepted: apiKeySetupDialog.saveAndContinue()
                background: Rectangle {
                    implicitHeight: 42
                    radius: 9
                    color: "#1d2028"
                    border.color: apiKeyInput.activeFocus ? accent : "#3a3f4c"
                }
            }
            CheckBox {
                id: showApiKey
                text: "显示 API Key"
                contentItem: Text {
                    leftPadding: showApiKey.indicator.width + showApiKey.spacing
                    text: showApiKey.text
                    color: textMuted
                    font.pixelSize: 11
                    verticalAlignment: Text.AlignVCenter
                }
            }
            Text { text: "接口地址（可选）"; color: "#d4d4d8"; font.pixelSize: 12 }
            TextField {
                id: apiBaseUrlInput
                Layout.fillWidth: true
                placeholderText: "留空使用 OpenAI 官方接口"
                selectByMouse: true
                color: textMain
                placeholderTextColor: "#71717a"
                onAccepted: apiKeySetupDialog.saveAndContinue()
                background: Rectangle {
                    implicitHeight: 42
                    radius: 9
                    color: "#1d2028"
                    border.color: apiBaseUrlInput.activeFocus ? accent : "#3a3f4c"
                }
            }
            Text {
                Layout.fillWidth: true
                text: "填写 API 根地址，例如 https://api.openai.com/v1。不要填写服务商网页、管理后台或完整的 /chat/completions 地址；若误填完整地址，保存时会自动修正。"
                color: "#858b98"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
            Text { text: "故事生成模型"; color: "#d4d4d8"; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    id: storyModelInput
                    Layout.fillWidth: true
                    placeholderText: "例如 gpt-4o-mini"
                    selectByMouse: true
                    color: textMain
                    placeholderTextColor: "#71717a"
                    background: Rectangle {
                        implicitHeight: 40
                        radius: 9
                        color: "#1d2028"
                        border.color: storyModelInput.activeFocus ? accent : "#3a3f4c"
                    }
                }
                GhostButton {
                    text: "选择模型"
                    onClicked: {
                        modelListDialog.selectionTarget = "story"
                        modelListDialog.open()
                        appController.fetchApiModels(apiKeyInput.text, apiBaseUrlInput.text)
                    }
                }
            }
            Text { text: "最终故事编辑模型（可选）"; color: "#d4d4d8"; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    id: editorModelInput
                    objectName: "editorModelInput"
                    Layout.fillWidth: true
                    placeholderText: "留空则复用故事生成模型"
                    selectByMouse: true
                    color: textMain
                    placeholderTextColor: "#71717a"
                    background: Rectangle {
                        implicitHeight: 40
                        radius: 9
                        color: "#1d2028"
                        border.color: editorModelInput.activeFocus ? accent : "#3a3f4c"
                    }
                }
                GhostButton {
                    text: "选择模型"
                    onClicked: {
                        modelListDialog.selectionTarget = "editor"
                        modelListDialog.open()
                        appController.fetchApiModels(apiKeyInput.text, apiBaseUrlInput.text)
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: "纯画面模式会先规划全片故事，再由最终编辑重写解说。这里适合选择更强的模型；留空则复用上面的故事模型。"
                color: "#858b98"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: reasoningSettingsContent.implicitHeight + 28
                radius: 12
                color: "#181b23"
                border.color: "#343946"
                ColumnLayout {
                    id: reasoningSettingsContent
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 9
                        Rectangle {
                            Layout.preferredWidth: 28; Layout.preferredHeight: 28
                            radius: 8; color: "#30234a"
                            Text { anchors.centerIn: parent; text: "✦"; color: accentLight; font.pixelSize: 13 }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            Text { text: "模型思考强度"; color: textMain; font.pixelSize: 13; font.bold: true }
                            Text { text: "模型不支持所选档位时会自动降级，不会阻断任务。"; color: textMuted; font.pixelSize: 10 }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        Text { text: "故事生成"; color: "#cfd2da"; font.pixelSize: 11; Layout.preferredWidth: 86 }
                        DarkComboBox {
                            id: storyReasoningCombo
                            Layout.fillWidth: true
                            model: ["标准（推荐）", "低 · 轻量推理", "中 · 平衡推理", "高 · 深度推理", "极高 · 极限推理"]
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        Text { text: "最终编辑"; color: "#cfd2da"; font.pixelSize: 11; Layout.preferredWidth: 86 }
                        DarkComboBox {
                            id: editorReasoningCombo
                            Layout.fillWidth: true
                            model: ["跟随故事生成", "低 · 轻量推理", "中 · 平衡推理", "高 · 深度推理", "极高 · 极限推理"]
                        }
                    }
                }
            }
            Text { text: "视觉理解模型"; color: "#d4d4d8"; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    id: visionModelInput
                    Layout.fillWidth: true
                    placeholderText: "请选择支持图片输入的模型"
                    selectByMouse: true
                    color: textMain
                    placeholderTextColor: "#71717a"
                    background: Rectangle {
                        implicitHeight: 40
                        radius: 9
                        color: "#1d2028"
                        border.color: visionModelInput.activeFocus ? accent : "#3a3f4c"
                    }
                }
                GhostButton {
                    text: "选择模型"
                    onClicked: {
                        modelListDialog.selectionTarget = "vision"
                        modelListDialog.open()
                        appController.fetchApiModels(apiKeyInput.text, apiBaseUrlInput.text)
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: "提示：中转站返回模型名称，并不代表每个模型都支持图片。视觉理解请选择明确支持视觉输入的模型。"
                color: "#d5a95f"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton {
                    text: "取消"
                    onClicked: apiKeySetupDialog.close()
                }
                FlatButton {
                    text: apiKeySetupDialog.requestedAction === "settings" ? "保存配置" : "保存并继续"
                    enabled: apiKeyInput.text.trim() !== ""
                    onClicked: apiKeySetupDialog.saveAndContinue()
                }
            }
            }
        }

        onOpened: {
            apiBaseUrlInput.text = appController.apiBaseUrl
            apiKeyInput.text = appController.apiKey
            storyModelInput.text = appController.storyApiModel
            editorModelInput.text = appController.storyEditorApiModel
            visionModelInput.text = appController.visionApiModel
            storyReasoningCombo.currentIndex = reasoningIndex(appController.storyReasoningEffort)
            editorReasoningCombo.currentIndex = reasoningIndex(appController.editorReasoningEffort)
            showApiKey.checked = false
            apiKeyInput.forceActiveFocus()
        }
        onClosed: apiKeyInput.text = ""
    }

    Dialog {
        id: modelListDialog
        property string selectionTarget: "story"
        property var filteredModels: {
            var query = modelSearchInput.text.trim().toLowerCase()
            if (query === "")
                return appController.apiModels
            return appController.apiModels.filter(function(item) {
                return String(item).toLowerCase().indexOf(query) >= 0
            })
        }
        width: 600
        height: Math.min(window.height - 70, 600)
        anchors.centerIn: parent
        modal: true
        padding: 20
        closePolicy: appController.apiModelsBusy ? Popup.NoAutoClose : Popup.CloseOnEscape

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#3a3f4c"
        }

        contentItem: ColumnLayout {
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text {
                        text: modelListDialog.selectionTarget === "vision"
                              ? "选择视觉理解模型"
                              : modelListDialog.selectionTarget === "editor"
                                ? "选择最终故事编辑模型"
                                : "选择故事生成模型"
                        color: textMain
                        font.pixelSize: 19
                        font.bold: true
                    }
                    Text {
                        text: appController.apiModelsStatus
                        color: appController.apiModels.length > 0 ? "#8ee3b4" : textMuted
                        font.pixelSize: 11
                    }
                }
                GhostButton {
                    text: appController.apiModelsBusy ? "获取中…" : "重新获取"
                    enabled: !appController.apiModelsBusy
                    onClicked: appController.fetchApiModels(apiKeyInput.text, apiBaseUrlInput.text)
                }
            }
            ProgressBar {
                Layout.fillWidth: true
                visible: appController.apiModelsBusy
                indeterminate: true
            }
            TextField {
                id: modelSearchInput
                Layout.fillWidth: true
                enabled: !appController.apiModelsBusy && appController.apiModels.length > 0
                placeholderText: "搜索模型名称"
                selectByMouse: true
                color: textMain
                placeholderTextColor: "#71717a"
                background: Rectangle {
                    implicitHeight: 40
                    radius: 9
                    color: "#1d2028"
                    border.color: modelSearchInput.activeFocus ? accent : "#3a3f4c"
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 10
                color: "#101218"
                border.color: "#2d313c"

                ListView {
                    id: availableModelsList
                    anchors.fill: parent
                    anchors.margins: 6
                    clip: true
                    spacing: 4
                    model: modelListDialog.filteredModels
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Rectangle {
                        required property string modelData
                        width: availableModelsList.width
                        height: 42
                        radius: 8
                        color: modelMouse.containsMouse ? "#29233a" : "transparent"
                        Text {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData
                            color: "#e5e7eb"
                            font.pixelSize: 12
                            elide: Text.ElideMiddle
                        }
                        MouseArea {
                            id: modelMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (modelListDialog.selectionTarget === "vision")
                                    visionModelInput.text = modelData
                                else if (modelListDialog.selectionTarget === "editor")
                                    editorModelInput.text = modelData
                                else
                                    storyModelInput.text = modelData
                                modelListDialog.close()
                            }
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: !appController.apiModelsBusy && modelListDialog.filteredModels.length === 0
                    text: appController.apiModelsStatus
                    color: textMuted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: "找不到需要的模型时，可关闭列表后手动输入模型名称。"
                    color: "#858b98"
                    font.pixelSize: 10
                }
                GhostButton {
                    text: "关闭"
                    enabled: !appController.apiModelsBusy
                    onClicked: modelListDialog.close()
                }
            }
        }

        onOpened: modelSearchInput.text = ""
    }

    Dialog {
        id: updateDialog
        width: 520
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: appController.updateBusy ? Popup.NoAutoClose : Popup.CloseOnEscape

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#3a3f4c"
        }

        contentItem: ColumnLayout {
            spacing: 14
            Text {
                text: "StoryCut 版本更新"
                color: textMain
                font.pixelSize: 19
                font.bold: true
            }
            Text {
                Layout.fillWidth: true
                text: appController.updateStatus
                color: appController.updateAvailable ? accentLight : textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                visible: appController.remoteNotes !== ""
                Layout.preferredHeight: visible ? Math.max(54, updateNotes.implicitHeight + 24) : 0
                radius: 9
                color: "#1d2028"
                border.color: "#303541"
                Text {
                    id: updateNotes
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    text: appController.remoteNotes
                    color: "#c6cad4"
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
            }
            Text {
                Layout.fillWidth: true
                visible: appController.updateAvailable
                text: "更新会优先在后台执行 Git fast-forward；无 Git 或 Git 不可用时，自动改用内置 ZIP 同步。两种方式都会更新 StoryCut 和启动器，但不会清理项目、未跟踪文件、用户设置、.env、模型或导出。"
                color: textMuted
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                GhostButton {
                    text: "关闭"
                    enabled: !appController.updateBusy
                    onClicked: updateDialog.close()
                }
                FlatButton {
                    visible: appController.updateAvailable
                    enabled: !appController.updateBusy
                    text: appController.updateBusy ? "正在安装…" : "下载并安装"
                    onClicked: appController.installUpdate()
                }
            }
        }
    }

    Connections {
        target: appController
        function onUpdateDialogRequested() {
            if (!updateDialog.opened)
                updateDialog.open()
        }
        function onSourceVideoRelinkRequested() {
            if (!relinkVideoDialog.visible)
                relinkVideoDialog.open()
        }
        function onQualityDialogRequested() {
            if (!qualityCheckDialog.opened)
                qualityCheckDialog.open()
        }
        function onDurationRevisionDialogRequested() {
            if (!durationRevisionDialog.opened)
                durationRevisionDialog.open()
        }
    }

    Dialog {
        id: canvasTransformDialog
        objectName: "canvasTransformDialog"
        width: Math.min(window.width - 80, 920)
        height: Math.min(window.height - 60, 680)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 18
            color: "#111319"
            border.color: "#343843"
        }

        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 22
            spacing: 14

            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { text: "裁剪拉伸"; color: textMain; font.pixelSize: 22; font.bold: true }
                    Text { text: "设置成片盒子比例，拖动视频四角调整大小；视频始终保持居中。"; color: textMuted; font.pixelSize: 11 }
                }
                GhostButton { text: "复位"; onClicked: appController.resetCanvasTransform() }
                GhostButton { text: "完成"; onClicked: canvasTransformDialog.close() }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#282c35" }

            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Text { text: "盒子宽高比"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 78 }
                Repeater {
                    model: [
                        { key: "fit", label: "适应（原片）" }, { key: "16:9", label: "16:9" },
                        { key: "4:3", label: "4:3" }, { key: "1:1", label: "1:1" },
                        { key: "3:4", label: "3:4" }, { key: "9:16", label: "9:16" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        Layout.preferredWidth: modelData.key === "fit" ? 102 : 58
                        Layout.preferredHeight: 32
                        radius: 8
                        color: appController.canvasAspectRatio === modelData.key ? "#50308a" : "#20232c"
                        border.color: appController.canvasAspectRatio === modelData.key ? accent : "#383d49"
                        Text { anchors.centerIn: parent; text: modelData.label; color: appController.canvasAspectRatio === modelData.key ? "white" : textMuted; font.pixelSize: 10; font.bold: true }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: appController.setCanvasAspectRatio(modelData.key) }
                    }
                }
                Item { Layout.fillWidth: true }
                Text { text: "固定比例拉伸"; color: textMain; font.pixelSize: 11 }
                ModernSwitch { checked: appController.canvasFixedScale; onToggled: appController.setCanvasFixedScale(checked) }
            }

            Rectangle {
                id: canvasEditorPanel
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 13
                color: "#1a1c22"
                border.color: "#2e323d"

                Item {
                    id: canvasEditorArea
                    anchors.fill: parent
                    anchors.margins: 20

                    Rectangle {
                        id: canvasBox
                        property real ratio: Math.max(0.1, appController.subtitleCanvasWidth / Math.max(1, appController.subtitleCanvasHeight))
                        property real availableWidth: canvasEditorArea.width * 0.82
                        property real availableHeight: canvasEditorArea.height * 0.82
                        property real areaRatio: availableWidth / Math.max(1, availableHeight)
                        width: ratio >= areaRatio ? availableWidth : availableHeight * ratio
                        height: ratio >= areaRatio ? availableWidth / ratio : availableHeight
                        anchors.centerIn: parent
                        color: "#030407"
                        border.width: 2
                        border.color: "#7f8491"
                        clip: true

                        Item {
                            id: canvasVideoLayer
                            property real sourceAspect: Math.max(0.1, appController.sourceVideoWidth / Math.max(1, appController.sourceVideoHeight))
                            property real baseWidth: canvasBox.width
                            property real baseHeight: baseWidth / sourceAspect
                            width: baseWidth * appController.canvasScaleX
                            height: baseHeight * appController.canvasScaleY
                            anchors.centerIn: parent

                            Image {
                                anchors.fill: parent
                                source: appController.subtitleCleanedPreviewUrl !== ""
                                        ? appController.subtitleCleanedPreviewUrl : appController.coverUrl
                                fillMode: Image.Stretch
                                asynchronous: true
                            }
                        }
                    }

                    Rectangle {
                        id: canvasSelectionFrame
                        property real sourceAspect: Math.max(0.1, appController.sourceVideoWidth / Math.max(1, appController.sourceVideoHeight))
                        property real baseWidth: canvasBox.width
                        property real baseHeight: baseWidth / sourceAspect
                        property real heightFillScale: canvasBox.height / Math.max(1, baseHeight)
                        property real snapDistance: 24
                        width: baseWidth * appController.canvasScaleX
                        height: baseHeight * appController.canvasScaleY
                        x: canvasBox.x + (canvasBox.width - width) / 2
                        y: canvasBox.y + (canvasBox.height - height) / 2
                        color: "transparent"
                        border.width: 2
                        border.color: "#f4f4f5"
                        z: 5

                        function snapHorizontalScale(value) {
                            var edgeDistance = Math.abs(value - 1.0) * baseWidth / 2
                            return edgeDistance <= snapDistance ? 1.0 : value
                        }

                        function snapVerticalScale(value) {
                            var edgeDistance = Math.abs(value - heightFillScale) * baseHeight / 2
                            return edgeDistance <= snapDistance
                                   ? heightFillScale : value
                        }

                        function snapUniformScale(value) {
                            var widthDistance = Math.abs(value - 1.0) * baseWidth / 2
                            var heightDistance = Math.abs(value - heightFillScale) * baseHeight / 2
                            if (widthDistance <= snapDistance && widthDistance <= heightDistance)
                                return 1.0
                            if (heightDistance <= snapDistance)
                                return heightFillScale
                            return value
                        }

                        Repeater {
                            model: [
                                { sx: -1, sy: -1 }, { sx: 1, sy: -1 },
                                { sx: -1, sy: 1 }, { sx: 1, sy: 1 }
                            ]
                            delegate: Rectangle {
                                required property var modelData
                                width: 10; height: 10; radius: 5
                                color: "#f4f4f5"; border.color: "#737986"
                                x: modelData.sx < 0 ? -width / 2 : canvasSelectionFrame.width - width / 2
                                y: modelData.sy < 0 ? -height / 2 : canvasSelectionFrame.height - height / 2
                                z: 6
                                MouseArea {
                                    anchors.fill: parent
                                    anchors.margins: -9
                                    cursorShape: modelData.sx === modelData.sy
                                                 ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor
                                    property point pressedAt
                                    property real startScaleX: 1
                                    property real startScaleY: 1
                                    onPressed: function(mouse) {
                                        pressedAt = mapToItem(canvasEditorArea, mouse.x, mouse.y)
                                        startScaleX = appController.canvasScaleX
                                        startScaleY = appController.canvasScaleY
                                    }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed)
                                            return
                                        var current = mapToItem(canvasEditorArea, mouse.x, mouse.y)
                                        var deltaX = (current.x - pressedAt.x) * modelData.sx * 2 / Math.max(1, canvasSelectionFrame.baseWidth)
                                        var deltaY = (current.y - pressedAt.y) * modelData.sy * 2 / Math.max(1, canvasSelectionFrame.baseHeight)
                                        if (appController.canvasFixedScale) {
                                            var delta = Math.abs(deltaX) >= Math.abs(deltaY) ? deltaX : deltaY
                                            var uniformScale = canvasSelectionFrame.snapUniformScale(startScaleX + delta)
                                            appController.setCanvasScale(uniformScale, uniformScale)
                                        } else {
                                            var horizontalScale = canvasSelectionFrame.snapHorizontalScale(startScaleX + deltaX)
                                            var verticalScale = canvasSelectionFrame.snapVerticalScale(startScaleY + deltaY)
                                            appController.setCanvasScale(horizontalScale, verticalScale)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true; spacing: 10
                Text { text: "宽度"; color: textMuted; font.pixelSize: 10 }
                Slider { Layout.fillWidth: true; from: 0.25; to: 10; stepSize: 0.01; value: appController.canvasScaleX; onMoved: appController.setCanvasScale(value, appController.canvasScaleY) }
                Text { text: Math.round(appController.canvasScaleX * 100) + "%"; color: accentLight; font.pixelSize: 10; Layout.preferredWidth: 42 }
                Text { text: "高度"; color: textMuted; font.pixelSize: 10 }
                Slider { Layout.fillWidth: true; from: 0.25; to: 10; stepSize: 0.01; value: appController.canvasScaleY; onMoved: appController.setCanvasScale(appController.canvasScaleX, value) }
                Text { text: Math.round(appController.canvasScaleY * 100) + "%"; color: accentLight; font.pixelSize: 10; Layout.preferredWidth: 42 }
            }
        }
    }

    Dialog {
        id: subtitleStyleDialog
        objectName: "subtitleStyleDialog"
        property string editMode: "style"
        width: Math.min(window.width - 70, 1040)
        height: Math.min(window.height - 50, 680)
        anchors.centerIn: parent
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            radius: 18
            color: "#111319"
            border.color: "#343843"
        }

        contentItem: Item {
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 24
                spacing: 18
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    Rectangle {
                        width: 46; height: 46; radius: 13
                        color: "#30254b"
                        border.color: "#614a8f"
                        Text { anchors.centerIn: parent; text: subtitleStyleDialog.editMode === "cleanup" ? "▤" : "Aa"; color: "#c4b5fd"; font.pixelSize: 17; font.bold: true }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text { text: subtitleStyleDialog.editMode === "cleanup" ? "清除原视频字幕" : "字幕样式与位置"; color: textMain; font.pixelSize: 22; font.bold: true }
                        Text {
                            text: subtitleStyleDialog.editMode === "cleanup"
                                  ? "设置原视频字幕的清除类型和范围；这里不会添加英文字幕"
                                  : "设置英文字幕字体、位置和底板，所有修改自动保存到当前项目"
                            color: textMuted; font.pixelSize: 11
                        }
                    }
                    Rectangle {
                        width: 74; height: 28; radius: 14
                        color: "#182820"
                        border.color: "#28563d"
                        Text { anchors.centerIn: parent; text: "自动保存"; color: "#72d6a1"; font.pixelSize: 10; font.bold: true }
                    }
                    GhostButton { text: "完成"; onClicked: subtitleStyleDialog.close() }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#282c35" }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 20

                    ColumnLayout {
                    Layout.preferredWidth: 540
                    Layout.minimumWidth: 450
                    Layout.maximumWidth: 570
                    Layout.fillHeight: true
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "实时预览"; color: textMain; font.pixelSize: 14; font.bold: true; Layout.fillWidth: true }
                        Text {
                            visible: subtitleStyleDialog.editMode === "style"
                            text: "预览源：" + appController.subtitleStylePreviewSourceName
                            color: appController.subtitleCleanedVideoReady ? "#63d39a" : textMuted
                            font.pixelSize: 9
                            elide: Text.ElideMiddle
                            Layout.maximumWidth: 210
                        }
                        GhostButton {
                            text: appController.subtitleEffectPreviewBusy ? "生成中…" : "查看真实预览"
                            enabled: !appController.subtitleEffectPreviewBusy
                            onClicked: subtitleStyleDialog.editMode === "cleanup"
                                       ? appController.generateSubtitleCleanupPreview()
                                       : appController.generateSubtitleEffectPreview()
                        }
                        Text { text: appController.resolutionText; color: textMuted; font.pixelSize: 10 }
                    }
                    Rectangle {
                        id: subtitlePreviewFrame
                        Layout.fillWidth: true
                        Layout.preferredHeight: 430
                        Layout.alignment: Qt.AlignTop
                        radius: 13
                        color: "#05060a"
                        clip: true
                        Item {
                            id: subtitleVideoViewport
                            property real sourceAspect: subtitleStyleDialog.editMode === "cleanup"
                                                        ? Math.max(1, appController.sourceVideoWidth) / Math.max(1, appController.sourceVideoHeight)
                                                        : Math.max(1, appController.subtitleCanvasWidth) / Math.max(1, appController.subtitleCanvasHeight)
                            property real frameAspect: subtitlePreviewFrame.width
                                                       / Math.max(1, subtitlePreviewFrame.height)
                            width: sourceAspect >= frameAspect
                                   ? subtitlePreviewFrame.width
                                   : subtitlePreviewFrame.height * sourceAspect
                            height: sourceAspect >= frameAspect
                                    ? subtitlePreviewFrame.width / sourceAspect
                                    : subtitlePreviewFrame.height
                            anchors.centerIn: parent
                            clip: true

                            Image {
                                anchors.fill: parent
                                source: appController.subtitleEffectPreviewReady
                                        ? appController.subtitleEffectPreviewUrl
                                        : subtitleStyleDialog.editMode === "cleanup"
                                          ? appController.coverUrl
                                          : appController.subtitleCleanedVideoReady
                                            ? appController.subtitleCleanedPreviewUrl
                                            : appController.coverUrl
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                visible: subtitleStyleDialog.editMode !== "cleanup"
                                         && (appController.exportFitMode === "vertical_blur"
                                          || (appController.exportFitMode === "vertical_crop"
                                              && appController.verticalCropFillPercent < 100))
                                         && !appController.subtitleEffectPreviewReady
                                opacity: 0.38
                            }
                            Image {
                                id: subtitleEffectImage
                                anchors.centerIn: parent
                                width: subtitleStyleDialog.editMode !== "cleanup"
                                       && !appController.subtitleEffectPreviewReady
                                       && appController.exportFitMode === "crop_stretch"
                                       ? parent.width * appController.canvasScaleX
                                       : parent.width
                                height: subtitleStyleDialog.editMode !== "cleanup"
                                        && !appController.subtitleEffectPreviewReady
                                        && appController.exportFitMode === "crop_stretch"
                                        ? parent.width
                                          / Math.max(0.1, appController.sourceVideoWidth / Math.max(1, appController.sourceVideoHeight))
                                          * appController.canvasScaleY
                                        : subtitleStyleDialog.editMode !== "cleanup"
                                        && !appController.subtitleEffectPreviewReady
                                        && appController.exportFitMode === "vertical_crop"
                                        && appController.verticalCropFillPercent < 100
                                        ? parent.height * appController.verticalCropFillPercent / 100
                                        : parent.height
                                source: appController.subtitleEffectPreviewReady
                                        ? appController.subtitleEffectPreviewUrl
                                        : subtitleStyleDialog.editMode === "cleanup"
                                          ? appController.coverUrl
                                          : appController.subtitleCleanedVideoReady
                                            ? appController.subtitleCleanedPreviewUrl
                                            : appController.coverUrl
                                fillMode: appController.subtitleEffectPreviewReady
                                          ? Image.Stretch
                                          : subtitleStyleDialog.editMode === "cleanup"
                                            ? Image.PreserveAspectCrop
                                          : appController.exportFitMode === "vertical_blur"
                                            ? Image.PreserveAspectFit
                                            : appController.exportFitMode === "vertical_crop"
                                              ? Image.PreserveAspectCrop
                                              : Image.Stretch
                                asynchronous: true
                            }
                            Rectangle {
                                visible: subtitleStyleDialog.editMode === "cleanup"
                                         && appController.subtitleStyle.cleanupMode !== "none"
                                x: parent.width * appController.subtitleStyle.cleanupX
                                width: parent.width * appController.subtitleStyle.cleanupWidth
                                y: parent.height * appController.subtitleStyle.cleanupY
                                height: parent.height * appController.subtitleStyle.cleanupHeight
                                color: appController.subtitleEffectPreviewReady ? "transparent"
                                       : appController.subtitleStyle.cleanupMode === "mask"
                                         ? Qt.rgba(0, 0, 0, appController.subtitleStyle.cleanupOpacity)
                                         : Qt.rgba(0.08, 0.08, 0.1, Math.max(0.32, appController.subtitleStyle.cleanupOpacity * 0.62))
                                Text {
                                    anchors.centerIn: parent
                                    text: appController.subtitleStyle.cleanupMode === "blur" ? "局部柔化 / 模糊"
                                          : appController.subtitleStyle.cleanupMode === "delogo" ? "Delogo 周边像素修复" : ""
                                    visible: text !== "" && !appController.subtitleEffectPreviewReady
                                    color: "#b8bbc5"
                                    font.pixelSize: 10
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.SizeAllCursor
                                    property point pressPoint
                                    property real startX: 0
                                    property real startY: 0
                                    property string lockedAxis: ""
                                    onPressed: function(mouse) {
                                        pressPoint = mapToItem(subtitleVideoViewport, mouse.x, mouse.y)
                                        startX = appController.subtitleStyle.cleanupX
                                        startY = appController.subtitleStyle.cleanupY
                                        lockedAxis = ""
                                    }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed)
                                            return
                                        var current = mapToItem(subtitleVideoViewport, mouse.x, mouse.y)
                                        var dx = current.x - pressPoint.x
                                        var dy = current.y - pressPoint.y
                                        if (lockedAxis === "") {
                                            if (Math.max(Math.abs(dx), Math.abs(dy)) < 7)
                                                return
                                            lockedAxis = Math.abs(dx) > Math.abs(dy) * 1.15 ? "x" : "y"
                                        }
                                        if (lockedAxis === "x") {
                                            var nextX = Math.max(0, Math.min(1 - appController.subtitleStyle.cleanupWidth,
                                                                            startX + dx / subtitleVideoViewport.width))
                                            var centerX = (1 - appController.subtitleStyle.cleanupWidth) / 2
                                            if (Math.abs(nextX) < 0.015)
                                                nextX = 0
                                            else if (Math.abs(nextX - centerX) < 0.015)
                                                nextX = centerX
                                            else if (Math.abs(nextX - (1 - appController.subtitleStyle.cleanupWidth)) < 0.015)
                                                nextX = 1 - appController.subtitleStyle.cleanupWidth
                                            appController.updateSubtitleStyle("cleanupX", nextX)
                                        } else {
                                            var nextY = Math.max(0, Math.min(1 - appController.subtitleStyle.cleanupHeight,
                                                                            startY + dy / subtitleVideoViewport.height))
                                            var centerY = (1 - appController.subtitleStyle.cleanupHeight) / 2
                                            if (Math.abs(nextY) < 0.015)
                                                nextY = 0
                                            else if (Math.abs(nextY - centerY) < 0.015)
                                                nextY = centerY
                                            else if (Math.abs(nextY - (1 - appController.subtitleStyle.cleanupHeight)) < 0.015)
                                                nextY = 1 - appController.subtitleStyle.cleanupHeight
                                            appController.updateSubtitleStyle("cleanupY", nextY)
                                        }
                                    }
                                }
                            }
                            Rectangle {
                                id: subtitlePreviewBubble
                                visible: subtitleStyleDialog.editMode === "style"
                                property real previewPadding: Math.max(
                                                                  5,
                                                                  appController.subtitleStyle.boxPadding
                                                                  / Math.max(1, appController.subtitleCanvasHeight)
                                                                  * subtitleVideoViewport.height)
                                width: parent.width * Math.max(
                                           0.2,
                                           1 - appController.subtitleStyle.horizontalMargin * 2
                                               / Math.max(1, appController.subtitleCanvasWidth))
                                height: subtitlePreviewLabel.implicitHeight + previewPadding * 2
                                x: (parent.width - width) / 2
                                y: Math.max(8, parent.height - height
                                            - appController.subtitleStyle.bottomMargin
                                              / Math.max(1, appController.subtitleCanvasHeight) * parent.height)
                                radius: appController.subtitleStyle.backgroundEnabled
                                        && appController.subtitleStyle.backgroundMode === "mask" ? 7 : 0
                                color: appController.subtitleStyle.backgroundEnabled
                                       && appController.subtitleStyle.backgroundMode === "mask"
                                       ? Qt.rgba(0, 0, 0, appController.subtitleStyle.backgroundOpacity)
                                       : "transparent"
                                Text {
                                    id: subtitlePreviewLabel
                                    width: parent.width - subtitlePreviewBubble.previewPadding * 2
                                    anchors.centerIn: parent
                                    text: appController.subtitlePreviewText
                                    color: appController.subtitleStyle.textColor.substring(0, 7)
                                    font.family: appController.subtitleStyle.fontFamily
                                    font.pixelSize: Math.max(
                                                        6,
                                                        appController.subtitleStyle.fontSize
                                                        / Math.max(1, appController.subtitleCanvasHeight)
                                                        * subtitleVideoViewport.height)
                                    font.bold: appController.subtitleStyle.bold
                                    font.italic: appController.subtitleStyle.italic
                                    font.letterSpacing: appController.subtitleStyle.letterSpacing
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.WordWrap
                                    style: Text.Outline
                                    styleColor: appController.subtitleStyle.outlineColor.substring(0, 7)
                                }
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            visible: appController.subtitleEffectPreviewBusy
                            color: "#8805060a"
                            Text {
                                anchors.centerIn: parent
                                text: "正在调用 FFmpeg 生成真实效果…"
                                color: "white"
                                font.pixelSize: 12
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 62
                        radius: 11
                        color: "#191c24"
                        border.color: "#2c303b"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            Text { text: "提示"; color: accentLight; font.pixelSize: 11; font.bold: true }
                            Text {
                                Layout.fillWidth: true
                                text: "预览按最终成片画布计算。裁剪画面可设置占画布高度；可拖动底板调整字幕安全位置。"
                                color: textMuted
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                    Rectangle {
                    Layout.preferredWidth: 400
                    Layout.minimumWidth: 350
                    Layout.maximumWidth: 430
                    Layout.fillHeight: true
                    radius: 13
                    color: "#171920"
                    border.color: "#292d38"
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 16
                        clip: true
                        contentWidth: availableWidth
                        visible: subtitleStyleDialog.editMode === "cleanup"
                        ColumnLayout {
                            width: parent.width
                            spacing: 13
                            Text { text: "字幕遮罩类型"; color: textMain; font.pixelSize: 13; font.bold: true }
                            Text { Layout.fillWidth: true; text: "这里只处理原视频字幕，不会生成或显示英文字幕。拖动画面中的选框可快速调整位置。"; color: textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
                            ComboBox {
                                Layout.fillWidth: true
                                model: ["黑色遮罩（最彻底）", "局部柔化 / 模糊", "Delogo 周边像素修复"]
                                currentIndex: appController.subtitleStyle.cleanupMode === "blur" ? 1 : appController.subtitleStyle.cleanupMode === "delogo" ? 2 : 0
                                onActivated: appController.updateSubtitleStyle("cleanupMode", currentIndex === 1 ? "blur" : currentIndex === 2 ? "delogo" : "mask")
                            }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2b2f39" }
                            Text { text: "遮罩区域"; color: textMain; font.pixelSize: 13; font.bold: true }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "左侧位置"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0; to: 0.8; stepSize: 0.005; value: appController.subtitleStyle.cleanupX; onMoved: appController.updateSubtitleStyle("cleanupX", value) }
                                PreciseSpinBox { from: 0; to: 160; divisor: 2; suffix: "%"; value: Math.round(appController.subtitleStyle.cleanupX * 200); onValueModified: appController.updateSubtitleStyle("cleanupX", value / 200) }
                            }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "顶部位置"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0; to: 0.94; stepSize: 0.005; value: appController.subtitleStyle.cleanupY; onMoved: appController.updateSubtitleStyle("cleanupY", value) }
                                PreciseSpinBox { from: 0; to: 188; divisor: 2; suffix: "%"; value: Math.round(appController.subtitleStyle.cleanupY * 200); onValueModified: appController.updateSubtitleStyle("cleanupY", value / 200) }
                            }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "区域宽度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0.1; to: 1; stepSize: 0.005; value: appController.subtitleStyle.cleanupWidth; onMoved: appController.updateSubtitleStyle("cleanupWidth", value) }
                                PreciseSpinBox { from: 20; to: 200; divisor: 2; suffix: "%"; value: Math.round(appController.subtitleStyle.cleanupWidth * 200); onValueModified: appController.updateSubtitleStyle("cleanupWidth", value / 200) }
                            }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "区域高度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0.02; to: 0.4; stepSize: 0.005; value: appController.subtitleStyle.cleanupHeight; onMoved: appController.updateSubtitleStyle("cleanupHeight", value) }
                                PreciseSpinBox { from: 4; to: 80; divisor: 2; suffix: "%"; value: Math.round(appController.subtitleStyle.cleanupHeight * 200); onValueModified: appController.updateSubtitleStyle("cleanupHeight", value / 200) }
                            }
                            RowLayout { Layout.fillWidth: true; visible: appController.subtitleStyle.cleanupMode === "mask"
                                Text { text: "遮罩浓度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0.25; to: 1; stepSize: 0.01; value: appController.subtitleStyle.cleanupOpacity; onMoved: appController.updateSubtitleStyle("cleanupOpacity", value) }
                                PreciseSpinBox { from: 25; to: 100; suffix: "%"; value: Math.round(appController.subtitleStyle.cleanupOpacity * 100); onValueModified: appController.updateSubtitleStyle("cleanupOpacity", value / 100) }
                            }
                            RowLayout { Layout.fillWidth: true; visible: appController.subtitleStyle.cleanupMode === "blur"
                                Text { text: "模糊强度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 1; to: 40; stepSize: 1; value: appController.subtitleStyle.blurRadius; onMoved: appController.updateSubtitleStyle("blurRadius", Math.round(value)) }
                                PreciseSpinBox { from: 1; to: 40; value: appController.subtitleStyle.blurRadius; onValueModified: appController.updateSubtitleStyle("blurRadius", value) }
                            }
                            RowLayout { Layout.fillWidth: true; visible: appController.subtitleStyle.cleanupMode === "blur" || appController.subtitleStyle.cleanupMode === "delogo"
                                Text { text: "向外扩展"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 90 }
                                Slider { Layout.fillWidth: true; from: 0; to: 80; stepSize: 1; value: appController.subtitleStyle.regionPadding; onMoved: appController.updateSubtitleStyle("regionPadding", Math.round(value)) }
                                PreciseSpinBox { from: 0; to: 80; suffix: " px"; value: appController.subtitleStyle.regionPadding; onValueModified: appController.updateSubtitleStyle("regionPadding", value) }
                            }
                            Text { Layout.fillWidth: true; text: "设置完成后关闭窗口，再点击第 3 步“生成去字幕视频”。生成文件保存在当前项目内，后续步骤会自动使用。"; color: accentLight; font.pixelSize: 10; wrapMode: Text.WordWrap }
                        }
                    }
                    ScrollView {
                        id: subtitleSettingsScroll
                        anchors.fill: parent
                        anchors.margins: 16
                        clip: true
                        contentWidth: availableWidth
                        visible: subtitleStyleDialog.editMode === "style"
                        ColumnLayout {
                            width: subtitleSettingsScroll.availableWidth
                            spacing: 13
                            Text { visible: false; text: "字幕底板类型"; color: textMain; font.pixelSize: 13; font.bold: true }
                            Text {
                                visible: false
                                text: "这一块底板同时遮住原字幕并衬托英文字幕，不会再叠加第二层字幕背景。"
                                color: textMuted
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            ComboBox {
                                visible: false
                                Layout.fillWidth: true
                                model: ["黑色遮罩（最彻底）", "局部柔化 / 模糊（推荐）", "Delogo 周边像素修复"]
                                currentIndex: appController.subtitleStyle.cleanupMode === "blur" ? 1
                                              : appController.subtitleStyle.cleanupMode === "delogo" ? 2 : 0
                                onActivated: appController.updateSubtitleStyle(
                                                 "cleanupMode",
                                                 currentIndex === 1 ? "blur" : currentIndex === 2 ? "delogo" : "mask")
                            }

                            Rectangle { visible: false; Layout.fillWidth: true; height: 1; color: "#2b2f39"; Layout.topMargin: 2; Layout.bottomMargin: 2 }
                            Text { text: "推荐预设"; color: textMain; font.pixelSize: 13; font.bold: true }
                            RowLayout {
                                Layout.fillWidth: true
                                GhostButton { text: "标准字幕"; onClicked: appController.applySubtitlePreset("box") }
                                GhostButton { text: "清爽描边"; onClicked: appController.applySubtitlePreset("outline") }
                                GhostButton { text: "Shorts 大字"; onClicked: appController.applySubtitlePreset("shorts") }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                GhostButton { text: "纪录片暖白"; onClicked: appController.applySubtitlePreset("documentary") }
                                GhostButton { text: "科普黄字"; onClicked: appController.applySubtitlePreset("science") }
                                GhostButton { text: "极简白字"; onClicked: appController.applySubtitlePreset("minimal") }
                            }

                            Text { text: "字体"; color: textMuted; font.pixelSize: 10 }
                            ComboBox {
                                Layout.fillWidth: true
                                model: ["Arial", "Verdana", "Tahoma", "Trebuchet MS", "Microsoft YaHei UI"]
                                currentIndex: Math.max(0, model.indexOf(appController.subtitleStyle.fontFamily))
                                onActivated: appController.updateSubtitleStyle("fontFamily", currentText)
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "字号"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 24; to: 88; stepSize: 1
                                    value: appController.subtitleStyle.fontSize
                                    onMoved: appController.updateSubtitleStyle("fontSize", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 24; to: 88; value: appController.subtitleStyle.fontSize; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("fontSize", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "底部距离"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 20; to: Math.max(120, appController.subtitleCanvasHeight * 0.55); stepSize: 2
                                    value: appController.subtitleStyle.bottomMargin
                                    onMoved: appController.updateSubtitleStyle("bottomMargin", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 20; to: Math.max(120, appController.subtitleCanvasHeight * 0.55)
                                    value: appController.subtitleStyle.bottomMargin; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("bottomMargin", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "左右边距"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 30; to: 240; stepSize: 2
                                    value: appController.subtitleStyle.horizontalMargin
                                    onMoved: appController.updateSubtitleStyle("horizontalMargin", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 30; to: 240; value: appController.subtitleStyle.horizontalMargin; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("horizontalMargin", value)
                                }
                            }

                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2b2f39"; Layout.topMargin: 2; Layout.bottomMargin: 2 }
                            Text { text: "文字背景"; color: textMain; font.pixelSize: 13; font.bold: true }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "显示背景"; color: textMain; font.pixelSize: 11; Layout.fillWidth: true }
                                Text { text: appController.subtitleStyle.backgroundEnabled ? "已开启" : "已关闭"; color: appController.subtitleStyle.backgroundEnabled ? "#63d39a" : textMuted; font.pixelSize: 10 }
                                ModernSwitch {
                                    checked: appController.subtitleStyle.backgroundEnabled
                                    onToggled: {
                                        appController.updateSubtitleStyle("backgroundEnabled", checked)
                                        subtitleBackgroundPreviewTimer.restart()
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.backgroundEnabled
                                Text { text: "背景类型"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                DarkComboBox {
                                    Layout.fillWidth: true
                                    model: ["黑色弹框", "局部柔化 / 模糊", "Delogo 周边像素修复"]
                                    currentIndex: appController.subtitleStyle.backgroundMode === "blur" ? 1
                                                  : appController.subtitleStyle.backgroundMode === "delogo" ? 2 : 0
                                    onActivated: {
                                        appController.updateSubtitleStyle(
                                            "backgroundMode",
                                            currentIndex === 1 ? "blur" : currentIndex === 2 ? "delogo" : "mask")
                                        subtitleBackgroundPreviewTimer.restart()
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.backgroundEnabled
                                         && appController.subtitleStyle.backgroundMode === "mask"
                                Text { text: "背景透明度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0.2; to: 0.95; stepSize: 0.01
                                    value: appController.subtitleStyle.backgroundOpacity
                                    onMoved: appController.updateSubtitleStyle("backgroundOpacity", value)
                                }
                                PreciseSpinBox {
                                    from: 20; to: 95; suffix: "%"
                                    value: Math.round(appController.subtitleStyle.backgroundOpacity * 100)
                                    onValueModified: appController.updateSubtitleStyle("backgroundOpacity", value / 100)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.backgroundEnabled
                                Text { text: "背景留白"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 2; to: 32; stepSize: 1
                                    value: appController.subtitleStyle.boxPadding
                                    onMoved: appController.updateSubtitleStyle("boxPadding", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 2; to: 32; suffix: " px"
                                    value: appController.subtitleStyle.boxPadding
                                    onValueModified: appController.updateSubtitleStyle("boxPadding", value)
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#2b2f39"; Layout.topMargin: 2; Layout.bottomMargin: 2 }

                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "描边粗细"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 8
                                    stepSize: 1
                                    value: appController.subtitleStyle.outlineWidth
                                    onMoved: appController.updateSubtitleStyle("outlineWidth", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 0; to: 8; value: appController.subtitleStyle.outlineWidth; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("outlineWidth", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "粗体"; color: textMain; font.pixelSize: 11 }
                                ModernSwitch {
                                    checked: appController.subtitleStyle.bold
                                    onToggled: appController.updateSubtitleStyle("bold", checked)
                                }
                                Item { Layout.preferredWidth: 18 }
                                Text { text: "斜体"; color: textMain; font.pixelSize: 11 }
                                ModernSwitch {
                                    checked: appController.subtitleStyle.italic
                                    onToggled: appController.updateSubtitleStyle("italic", checked)
                                }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "文字颜色"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Rectangle {
                                    Layout.preferredWidth: 30; Layout.preferredHeight: 24; radius: 6
                                    color: appController.subtitleStyle.textColor.substring(0, 7)
                                    border.color: "#5b6070"
                                }
                                GhostButton {
                                    text: "选择"
                                    onClicked: {
                                        subtitleTextColorDialog.selectedColor = appController.subtitleStyle.textColor.substring(0, 7)
                                        subtitleTextColorDialog.open()
                                    }
                                }
                                Text { text: appController.subtitleStyle.textColor; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "描边颜色"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Rectangle {
                                    Layout.preferredWidth: 30; Layout.preferredHeight: 24; radius: 6
                                    color: appController.subtitleStyle.outlineColor.substring(0, 7)
                                    border.color: "#5b6070"
                                }
                                GhostButton {
                                    text: "选择"
                                    onClicked: {
                                        subtitleOutlineColorDialog.selectedColor = appController.subtitleStyle.outlineColor.substring(0, 7)
                                        subtitleOutlineColorDialog.open()
                                    }
                                }
                                Text { text: appController.subtitleStyle.outlineColor; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "阴影大小"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0; to: 10; stepSize: 1
                                    value: appController.subtitleStyle.shadow
                                    onMoved: appController.updateSubtitleStyle("shadow", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 0; to: 10; value: appController.subtitleStyle.shadow; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("shadow", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "字间距"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: -5; to: 20; stepSize: 1
                                    value: appController.subtitleStyle.letterSpacing
                                    onMoved: appController.updateSubtitleStyle("letterSpacing", value)
                                }
                                PreciseSpinBox {
                                    from: -5; to: 20; value: appController.subtitleStyle.letterSpacing; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("letterSpacing", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "字幕动画"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                ComboBox {
                                    Layout.fillWidth: true
                                    model: ["无动画（最稳定）", "柔和淡入淡出", "轻微弹出"]
                                    currentIndex: appController.subtitleStyle.animation === "none" ? 0
                                                  : appController.subtitleStyle.animation === "pop" ? 2 : 1
                                    onActivated: appController.updateSubtitleStyle(
                                                     "animation", currentIndex === 0 ? "none" : currentIndex === 2 ? "pop" : "fade")
                                }
                            }

                        }
                    }
                    }
                }
            }
        }
    }

    component FlatButton: Button {
        id: control
        implicitHeight: 42
        leftPadding: 18
        rightPadding: 18
        contentItem: Text {
            text: control.text
            color: control.enabled ? "#ffffff" : "#71717a"
            font.pixelSize: 14
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: control.down ? "#6d28d9" : control.hovered ? accentLight : accent
            opacity: control.enabled ? 1 : 0.45
        }
    }

    component GhostButton: Button {
        id: control
        property color foreground: "#d4d4d8"
        implicitHeight: 40
        leftPadding: 16
        rightPadding: 16
        contentItem: Text {
            text: control.text
            color: control.hovered ? textMain : control.foreground
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 9
            color: control.hovered ? "#292d38" : "transparent"
            border.color: "#343843"
        }
    }

    component ModernSwitch: Switch {
        id: toggleControl
        implicitWidth: 46
        implicitHeight: 26
        padding: 0
        spacing: 0

        indicator: Rectangle {
            anchors.fill: parent
            radius: height / 2
            color: toggleControl.checked ? "#7046d8" : "#2b2f39"
            border.width: 1
            border.color: toggleControl.checked
                          ? (toggleControl.hovered ? "#bda4ff" : "#9368f2")
                          : (toggleControl.hovered ? "#5a6070" : "#414653")
            opacity: toggleControl.enabled ? 1 : 0.48
            Behavior on color { ColorAnimation { duration: 140 } }

            Rectangle {
                width: 20
                height: 20
                radius: 10
                y: 3
                x: toggleControl.checked ? parent.width - width - 3 : 3
                color: toggleControl.enabled ? "#ffffff" : "#a5a8b0"
                Behavior on x {
                    NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
                }
            }
        }
        contentItem: Item { }
        background: Item { }
    }

    component DarkComboBox: ComboBox {
        id: comboControl
        implicitHeight: 40
        leftPadding: 13
        rightPadding: 36
        font.pixelSize: 12

        contentItem: Text {
            text: comboControl.displayText
            color: comboControl.enabled ? textMain : "#6f7480"
            font: comboControl.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        indicator: Text {
            x: comboControl.width - width - 13
            anchors.verticalCenter: parent.verticalCenter
            text: "▾"
            color: comboControl.popup.visible ? accentLight : textMuted
            font.pixelSize: 12
        }
        background: Rectangle {
            radius: 9
            color: comboControl.down ? "#25212f" : comboControl.hovered ? "#232631" : "#1b1e26"
            border.width: 1
            border.color: comboControl.activeFocus || comboControl.popup.visible ? accent : "#383d49"
        }
        delegate: ItemDelegate {
            width: comboControl.width
            implicitHeight: 38
            highlighted: comboControl.highlightedIndex === index
            contentItem: Text {
                text: modelData
                color: highlighted ? "#ffffff" : "#d2d5dd"
                font.pixelSize: 11
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 7
                color: highlighted ? "#38275a" : hovered ? "#242832" : "transparent"
            }
        }
        popup: Popup {
            y: comboControl.height + 5
            width: comboControl.width
            implicitHeight: Math.min(contentItem.implicitHeight + 12, 230)
            padding: 6
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: comboControl.popup.visible ? comboControl.delegateModel : null
                currentIndex: comboControl.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator { }
            }
            background: Rectangle {
                radius: 10
                color: "#151820"
                border.color: "#444957"
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 244
            Layout.fillHeight: true
            color: "#101218"
            border.color: "#20232c"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 28
                    Rectangle {
                        width: 38; height: 38; radius: 11
                        gradient: Gradient {
                            GradientStop { position: 0; color: "#a78bfa" }
                            GradientStop { position: 1; color: "#6d28d9" }
                        }
                        Text { anchors.centerIn: parent; text: "S"; color: "white"; font.pixelSize: 20; font.bold: true }
                    }
                    Column {
                        Layout.fillWidth: true
                        Text { text: "StoryCut"; color: textMain; font.pixelSize: 18; font.bold: true }
                        Text { text: "AI 解说剪辑台"; color: textMuted; font.pixelSize: 11 }
                    }
                }

                Repeater {
                    model: [
                        { icon: "⌂", label: "项目首页" },
                        { icon: manuscriptProject ? "文" : "✦", label: manuscriptProject ? "整理文稿" : "理解原片" },
                        { icon: "☷", label: manuscriptProject ? "规划分镜" : "组织故事" },
                        { icon: "◫", label: manuscriptProject ? "素材匹配" : "镜头匹配" },
                        { icon: "⇧", label: manuscriptProject ? "配音导出" : "导出成片" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        property bool completed: index === 1 ? understandingDone
                                                   : index === 2 ? storyDone
                                                   : index === 3 ? matchingDone
                                                   : index === 4 ? exportDone
                                                   : false
                        property bool unlocked: index === 0
                                                || (index === 1 && appController.hasProject)
                                                || (index === 2 && understandingDone)
                                                || (index === 3 && storyDone)
                                                || (index === 4 && matchingDone)
                        Layout.fillWidth: true
                        height: 46
                        radius: 10
                        color: completed
                               ? (currentStep === index ? "#214633" : navMouse.containsMouse ? "#1c372a" : "#172b22")
                               : currentStep === index ? "#2b2145"
                               : navMouse.containsMouse ? "#1b1e27" : "transparent"
                        border.width: completed ? 1 : 0
                        border.color: "#326b4d"
                        RowLayout {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 14
                            anchors.rightMargin: 28
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 13
                            Text {
                                text: modelData.icon
                                color: completed ? "#8ee3b4" : currentStep === index ? accentLight : unlocked ? textMuted : "#545965"
                                font.pixelSize: 18
                            }
                            Text {
                                text: modelData.label
                                color: completed ? "#b8f3cf" : currentStep === index ? "#ede9fe" : unlocked ? "#c4c6cf" : "#6e7380"
                                font.pixelSize: 14
                                Layout.fillWidth: true
                            }
                            Rectangle {
                                visible: index > 0
                                Layout.preferredWidth: 22
                                Layout.minimumWidth: 22
                                Layout.maximumWidth: 22
                                Layout.preferredHeight: 22
                                radius: 11
                                color: completed ? "#2d6a4b" : unlocked ? "#30264a" : "#20232b"
                                border.width: 1
                                border.color: completed ? "#65b98a" : unlocked ? "#59417f" : "#343843"
                                Text {
                                    anchors.centerIn: parent
                                    text: completed ? "✓" : index
                                    color: completed ? "#d5f8e3" : unlocked ? "#bca1e8" : "#686d78"
                                    font.pixelSize: completed ? 12 : 9
                                    font.bold: true
                                }
                            }
                        }
                        MouseArea {
                            id: navMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (index === 0) scrollToSection(homeSection, 0)
                                else if (index === 1) scrollToSection(understandingSection, 1)
                                else if (index === 2) scrollToSection(storySection, 2)
                                else if (index === 3) scrollToSection(matchingSection, 3)
                                else scrollToSection(exportSection, 4)
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#292c35" }
                Text { text: "StoryCut  v" + appController.appVersion; color: "#626773"; font.pixelSize: 11; Layout.topMargin: 12 }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: window.color

            ScrollView {
                id: mainScroll
                objectName: "mainScroll"
                anchors.fill: parent
                contentWidth: availableWidth
                contentHeight: mainContent.implicitHeight + mainContent.y + 30
                ScrollBar.vertical.policy: ScrollBar.AsNeeded

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    propagateComposedEvents: true
                    onWheel: function(event) {
                        var flickable = mainScroll.contentItem
                        var delta = event.pixelDelta.y !== 0
                                  ? event.pixelDelta.y * 2.2
                                  : event.angleDelta.y / 120 * 150
                        var maximum = Math.max(0, flickable.contentHeight - flickable.height)
                        flickable.contentY = Math.max(0, Math.min(maximum, flickable.contentY - delta))
                        event.accepted = true
                    }
                }

                ColumnLayout {
                    id: mainContent
                    x: 38
                    y: 30
                    width: parent.width - 76
                    spacing: 22

                    RowLayout {
                        id: homeSection
                        Layout.fillWidth: true
                        ColumnLayout {
                            spacing: 5
                            Text { text: manuscriptProject ? "把一篇文稿，变成一个完整画面故事" : "下午好，开始一个新故事"; color: textMain; font.pixelSize: 27; font.weight: Font.DemiBold }
                            Text { text: appController.notice; color: textMuted; font.pixelSize: 14 }
                            RowLayout {
                                visible: appController.hasProject
                                spacing: 7
                                Text { text: "项目名"; color: textMuted; font.pixelSize: 11 }
                                TextField {
                                    id: projectNameInput
                                    Layout.preferredWidth: 220
                                    implicitHeight: 36
                                    text: appController.projectName
                                    maximumLength: 64
                                    selectByMouse: true
                                    color: textMain
                                    font.pixelSize: 13
                                    onAccepted: {
                                        appController.renameProject(text)
                                        focus = false
                                    }
                                    onEditingFinished: appController.renameProject(text)
                                    background: Rectangle {
                                        radius: 9
                                        color: "#171922"
                                        border.color: projectNameInput.activeFocus ? accent : "#343843"
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.text: "可直接修改项目名，按 Enter 保存"
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                        GhostButton {
                            text: appController.updateBusy
                                  ? "检查中…"
                                  : appController.updateAvailable
                                    ? "●  可更新 v" + appController.remoteVersion
                                    : "检查更新"
                            foreground: appController.updateAvailable ? "#86efac" : "#d4d4d8"
                            enabled: !appController.updateBusy
                            onClicked: appController.checkForUpdates()
                        }
                        GhostButton {
                            text: appController.apiConfigured ? "●  API 已配置" : "API 设置"
                            foreground: appController.apiConfigured ? "#86efac" : "#d4d4d8"
                            onClicked: {
                                apiKeySetupDialog.requestedAction = "settings"
                                apiKeySetupDialog.open()
                            }
                        }
                        GhostButton {
                            objectName: "recentProjectsButton"
                            text: "最近项目"
                            onClicked: recentProjectsDialog.open()
                        }
                        FlatButton { text: "+  创建项目"; onClicked: projectTypeDialog.open() }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 215
                        radius: 18
                        color: panel
                        border.color: "#292c36"

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 24
                            spacing: 28

                            Rectangle {
                                Layout.preferredWidth: 285
                                Layout.fillHeight: true
                                radius: 14
                                color: "#0e1015"
                                border.color: "#34303f"
                                Image {
                                    anchors.fill: parent
                                    anchors.margins: 2
                                    source: appController.coverUrl
                                    fillMode: Image.PreserveAspectCrop
                                    visible: source.toString() !== ""
                                    asynchronous: true
                                    cache: false
                                    opacity: 0.78
                                }
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 14
                                    color: "#75080a0f"
                                    visible: appController.coverUrl !== ""
                                }
                                Rectangle { anchors.fill: parent; anchors.margins: 1; radius: 13; color: "transparent"; border.color: "#221b32" }
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 10
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: manuscriptProject ? "文" : appController.mediaBusy ? "◌" : appController.videoPath ? "▶" : "＋"; color: accentLight; font.pixelSize: manuscriptProject ? 30 : 34; font.bold: manuscriptProject }
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: manuscriptProject ? "纯文稿项目" : appController.mediaBusy ? "正在生成封面…" : appController.videoPath ? "视频已导入" : "选择视频或文稿开始"; color: textMain; font.pixelSize: 14; font.bold: true }
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: manuscriptProject ? appController.sourceManuscriptCharCount + " 字符 · 原稿已保存" : appController.videoPath ? appController.durationText + "  ·  " + appController.resolutionText : "MP4 · MKV · TXT · Markdown"; color: "#d1d5db"; font.pixelSize: 11 }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: {
                                        if (manuscriptProject)
                                            scrollToSection(understandingSection, 1)
                                        else if (appController.videoPath)
                                            previewDialog.open()
                                        else
                                            projectTypeDialog.open()
                                    }
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                spacing: 10
                                Text {
                                    id: importedVideoTitle
                                    Layout.fillWidth: true
                                    text: appController.projectName
                                    color: textMain
                                    font.pixelSize: 21
                                    font.bold: true
                                    wrapMode: Text.WrapAnywhere
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                    ToolTip.visible: importedVideoTitleHover.hovered && appController.videoPath !== ""
                                    ToolTip.text: appController.videoPath
                                    HoverHandler { id: importedVideoTitleHover }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: manuscriptProject
                                          ? appController.sourceManuscriptText
                                          : appController.videoPath || "从已有视频开始，或直接用一篇文稿规划分镜和素材。"
                                    color: textMuted
                                    font.pixelSize: 13
                                    wrapMode: Text.WrapAnywhere
                                    maximumLineCount: 2
                                    elide: Text.ElideMiddle
                                }
                                Item { Layout.fillHeight: true }
                                RowLayout {
                                    spacing: 10
                                    Rectangle { width: manuscriptProject ? 102 : 82; height: 27; radius: 7; color: "#20242c"; Text { anchors.centerIn: parent; text: manuscriptProject ? "纯文稿模式" : appController.durationText; color: "#c7cad2"; font.pixelSize: 11 } }
                                    Rectangle { width: 96; height: 27; radius: 7; color: "#20242c"; visible: !manuscriptProject; Text { anchors.centerIn: parent; text: appController.resolutionText; color: "#c7cad2"; font.pixelSize: 11 } }
                                    Rectangle { width: 116; height: 27; radius: 7; color: "#20242c"; visible: !manuscriptProject && appController.codecText !== ""; Text { anchors.centerIn: parent; text: appController.codecText; color: "#c7cad2"; font.pixelSize: 10 } }
                                    Item { Layout.fillWidth: true }
                                    FlatButton {
                                        text: manuscriptProject ? "编辑文稿  →" : appController.mediaBusy ? "正在读取…" : appController.videoPath ? "更换视频" : "创建项目  →"
                                        enabled: !appController.mediaBusy
                                        onClicked: manuscriptProject ? scrollToSection(understandingSection, 1) : appController.videoPath ? videoDialog.open() : projectTypeDialog.open()
                                    }
                                }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 4
                        columnSpacing: 14
                        rowSpacing: 14

                        Repeater {
                            model: [
                                { n: "01", title: manuscriptProject ? "整理文稿" : "理解原片", desc: manuscriptProject ? "保存原稿，确认内容与目标" : "语音识别、场景检测与画面理解", state: understandingDone ? "已完成" : appController.hasProject ? "可开始" : "等待创建项目" },
                                { n: "02", title: manuscriptProject ? "规划分镜" : "组织故事", desc: manuscriptProject ? "拆分旁白，明确每段需要的画面" : "挑选关键事件，生成精简叙事", state: storyDone ? "已完成" : understandingDone ? "可开始" : manuscriptProject ? "等待整理文稿" : "等待理解原片" },
                                { n: "03", title: manuscriptProject ? "匹配素材" : "匹配镜头", desc: manuscriptProject ? "导入素材，自动寻找最佳候选片段" : "为每句解说寻找最佳原片段", state: matchingDone ? "已完成" : storyDone ? "可开始" : manuscriptProject ? "等待规划分镜" : "等待组织故事" },
                                { n: "04", title: manuscriptProject ? "配音导出" : "预览导出", desc: manuscriptProject ? "确认配音、字幕与素材并输出成片" : "确认配音、字幕与镜头并输出成片", state: exportDone ? "预览已生成" : matchingDone ? "可开始" : manuscriptProject ? "等待素材匹配" : "等待镜头匹配" }
                            ]
                            delegate: Rectangle {
                                id: overviewCard
                                required property var modelData
                                required property int index
                                property bool completed: index === 0 ? understandingDone
                                                           : index === 1 ? storyDone
                                                           : index === 2 ? matchingDone
                                                           : exportDone
                                Layout.fillWidth: true
                                Layout.preferredHeight: 154
                                radius: 14
                                color: completed ? "#14241d" : panel
                                border.color: completed ? "#326b4d" : cardMouse.containsMouse ? "#51436d" : "#292c36"
                                Column {
                                    anchors.fill: parent
                                    anchors.margins: 17
                                    spacing: 8
                                    StepBadge {
                                        stepNumber: modelData.n
                                        completed: overviewCard.completed
                                        width: 58
                                        height: 44
                                    }
                                    Text { text: modelData.title; color: textMain; font.pixelSize: 16; font.bold: true }
                                    Text { width: parent.width; text: modelData.desc; color: textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                                    Text { text: modelData.state; color: completed ? "#8ee3b4" : "#737986"; font.pixelSize: 11; font.bold: completed }
                                }
                                MouseArea {
                                    id: cardMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (index === 0) scrollToSection(understandingSection, 1)
                                        else if (index === 1) scrollToSection(storySection, 2)
                                        else if (index === 2) scrollToSection(matchingSection, 3)
                                        else scrollToSection(exportSection, 4)
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: understandingSection
                        property bool expanded: true
                        Layout.fillWidth: true
                        Layout.preferredHeight: expanded ? understandingContent.implicitHeight + 36 : 90
                        radius: 14
                        color: panel
                        border.color: understandingDone ? "#326b4d" : "#292c36"

                        ColumnLayout {
                            id: understandingContent
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 14

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16
                                StepBadge { stepNumber: "01"; completed: understandingDone }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text { text: manuscriptProject ? "整理文稿" : "理解原片"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: manuscriptProject
                                              ? "原稿保存在当前项目中；编辑停止约 0.7 秒后自动保存。"
                                              : understandingDone
                                                ? appController.analysisStatus
                                                : appController.videoPath
                                                  ? "识别语音、检测场景并理解关键画面。"
                                                  : "请先创建视频项目，随后即可开始原片理解。"
                                        color: understandingDone ? "#8ee3b4" : textMuted
                                        font.pixelSize: 12
                                    }
                                    TapHandler { onTapped: understandingSection.expanded = !understandingSection.expanded }
                                }
                                GhostButton {
                                    text: understandingSection.expanded ? "收起  ▴" : "展开  ▾"
                                    onClicked: understandingSection.expanded = !understandingSection.expanded
                                }
                                Rectangle {
                                    visible: manuscriptProject
                                    Layout.preferredWidth: 82
                                    Layout.preferredHeight: 30
                                    radius: 9
                                    color: "#183729"
                                    border.color: "#347456"
                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓ 自动保存"
                                        color: "#8ee3b4"
                                        font.pixelSize: 10
                                        font.bold: true
                                    }
                                }
                                FlatButton {
                                    visible: !manuscriptProject
                                    text: appController.analysisBusy ? "正在理解原片…" : understandingDone ? "重新理解原片" : appController.videoPath ? "开始理解原片  →" : "请先选择视频"
                                    enabled: appController.videoPath !== "" && !appController.analysisBusy
                                    onClicked: {
                                        if (appController.refreshApiConfiguration()) {
                                            appController.startUnderstanding()
                                        } else {
                                            apiPreflightDialog.requestedAction = "understanding"
                                            apiPreflightDialog.open()
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: understandingSection.expanded && !manuscriptProject
                                spacing: 9
                                Text { text: "内容类型"; color: textMuted; font.pixelSize: 11; Layout.rightMargin: 4 }
                                Repeater {
                                    model: [
                                        { value: "speech", label: "语音与画面" },
                                        { value: "visual", label: "纯画面叙事" }
                                    ]
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.preferredWidth: 112
                                        Layout.preferredHeight: 30
                                        radius: 8
                                        color: appController.analysisContentMode === modelData.value ? "#6d28d9" : "#252832"
                                        border.color: appController.analysisContentMode === modelData.value ? accentLight : "#343843"
                                        Text { anchors.centerIn: parent; text: modelData.label; color: "white"; font.pixelSize: 11 }
                                        MouseArea {
                                            anchors.fill: parent
                                            enabled: !appController.analysisBusy
                                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                            onClicked: appController.setAnalysisContentMode(modelData.value)
                                        }
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: appController.analysisContentModeHint
                                    color: "#858b98"
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: visible ? 76 : 0
                                visible: understandingSection.expanded && appController.hasProject
                                radius: 11
                                color: appController.seriesSplitEnabled ? "#18251f" : "#171a21"
                                border.color: appController.seriesSplitEnabled
                                              ? "#397456"
                                              : appController.seriesSplitSuggested ? "#72582f" : "#303540"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    spacing: 12
                                    Rectangle {
                                        Layout.preferredWidth: 38
                                        Layout.preferredHeight: 38
                                        radius: 10
                                        color: appController.seriesSplitEnabled ? "#204330" : "#292631"
                                        Text {
                                            anchors.centerIn: parent
                                            text: appController.seriesSplitEnabled ? "Ⅱ" : "1"
                                            color: appController.seriesSplitEnabled ? "#8ee3b4" : accentLight
                                            font.pixelSize: 14
                                            font.bold: true
                                        }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 3
                                        RowLayout {
                                            spacing: 8
                                            Text { text: "允许自动拆分多集"; color: textMain; font.pixelSize: 12; font.bold: true }
                                            Rectangle {
                                                visible: appController.seriesSplitSuggested && !appController.seriesSplitEnabled
                                                Layout.preferredWidth: splitSuggestionText.implicitWidth + 14
                                                Layout.preferredHeight: 22
                                                radius: 7
                                                color: "#3b2d1b"
                                                Text {
                                                    id: splitSuggestionText
                                                    anchors.centerIn: parent
                                                    text: "建议考虑"
                                                    color: "#e9bd73"
                                                    font.pixelSize: 9
                                                    font.bold: true
                                                }
                                            }
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: appController.seriesSplitHint
                                            color: textMuted
                                            font.pixelSize: 10
                                            elide: Text.ElideRight
                                        }
                                    }
                                    ModernSwitch {
                                        checked: appController.seriesSplitEnabled
                                        onToggled: appController.setSeriesSplitEnabled(checked)
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 280
                                visible: understandingSection.expanded && manuscriptProject
                                radius: 12
                                color: "#0d0f14"
                                border.color: sourceManuscriptArea.activeFocus ? accent : "#30333d"
                                ScrollView {
                                    anchors.fill: parent
                                    anchors.margins: 3
                                    TextArea {
                                        id: sourceManuscriptArea
                                        text: appController.sourceManuscriptText
                                        color: textMain
                                        font.pixelSize: 14
                                        wrapMode: TextEdit.Wrap
                                        selectByMouse: true
                                        padding: 15
                                        background: null
                                        onTextChanged: {
                                            if (activeFocus && manuscriptProject)
                                                manuscriptAutosaveTimer.restart()
                                        }
                                        onActiveFocusChanged: {
                                            if (!activeFocus && manuscriptProject
                                                    && text.trim().length > 0
                                                    && text !== appController.sourceManuscriptText) {
                                                manuscriptAutosaveTimer.stop()
                                                appController.updateSourceManuscript(text)
                                            }
                                        }
                                    }
                                }
                                Rectangle {
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    anchors.margins: 10
                                    width: manuscriptCountText.implicitWidth + 18
                                    height: 26
                                    radius: 8
                                    color: "#dd1b1e27"
                                    Text {
                                        id: manuscriptCountText
                                        anchors.centerIn: parent
                                        text: sourceManuscriptArea.text.trim().length + " 字符 · 自动保存"
                                        color: "#9da3af"
                                        font.pixelSize: 10
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: understandingSection.expanded && !manuscriptProject && (appController.analysisBusy || appController.analysisProgress > 0)
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: appController.analysisElapsedText; color: textMuted; font.pixelSize: 11 }
                                    Text { text: "·"; color: "#575b66"; font.pixelSize: 11 }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 3
                                        Text { text: appController.analysisEtaText; color: accentLight; font.pixelSize: 11 }
                                        ProcessingDots {
                                            visible: appController.analysisBusy && !appController.analysisEtaReliable
                                            Layout.preferredWidth: 22
                                            Layout.preferredHeight: 14
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                    Text { text: Math.round(appController.analysisProgress * 100) + "%"; color: understandingDone ? "#8ee3b4" : accentLight; font.pixelSize: 12; font.bold: true }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: appController.modelDownloadHint
                                    color: "#717784"
                                    font.pixelSize: 10
                                    wrapMode: Text.WordWrap
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: appController.modelDownloadVisible
                                    spacing: 5
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text {
                                            Layout.fillWidth: true
                                            text: appController.modelDownloadStatus
                                            color: accentLight
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            text: Math.round(appController.modelDownloadProgress * 100) + "%"
                                            color: accentLight
                                            font.pixelSize: 11
                                            font.bold: true
                                        }
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 6
                                        radius: 3
                                        color: "#30333d"
                                        Rectangle {
                                            width: parent.width * appController.modelDownloadProgress
                                            height: parent.height
                                            radius: 3
                                            color: "#42b883"
                                        }
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 6
                                    radius: 3
                                    color: "#30333d"
                                    Rectangle {
                                        width: parent.width * appController.analysisProgress
                                        height: parent.height
                                        radius: 3
                                        color: understandingDone ? "#58b982" : accent
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: storySection
                        property bool expanded: true
                        Layout.fillWidth: true
                        Layout.preferredHeight: expanded ? storyContent.implicitHeight + 36 : 90
                        radius: 14
                        color: panel
                        border.color: storyDone ? "#326b4d" : "#292c36"

                        ColumnLayout {
                            id: storyContent
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 14

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16
                                StepBadge { stepNumber: "02"; completed: storyDone }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text { text: manuscriptProject ? "规划分镜" : "组织故事"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: storyDone
                                              ? appController.storyStatus
                                              : manuscriptProject
                                                ? appController.storyboardStale
                                                  ? appController.storyStatus
                                                  : "文稿已经就绪；在这里生成英文解说和逐段镜头需求。"
                                                : understandingDone
                                                  ? "选择期望时长，生成自然英文解说与故事结构；最终严格控制在三分钟内。"
                                                  : "完成第 1 步理解原片后，才能生成故事与英文解说。"
                                        color: storyDone ? "#8ee3b4" : textMuted
                                        font.pixelSize: 12
                                    }
                                    TapHandler { onTapped: storySection.expanded = !storySection.expanded }
                                }
                                GhostButton {
                                    text: storySection.expanded ? "收起  ▴" : "展开  ▾"
                                    onClicked: storySection.expanded = !storySection.expanded
                                }
                                FlatButton {
                                    text: manuscriptProject
                                          ? appController.storyBusy ? "正在规划分镜…" : storyDone ? "重新规划英文解说与分镜" : "生成英文解说与分镜  →"
                                          : appController.storyBusy ? "正在组织故事…" : storyDone ? "重新生成故事" : understandingDone ? "生成故事与英文解说  →" : "等待理解原片"
                                    enabled: understandingDone && !appController.storyBusy && !appController.factReviewBusy && !appController.terminologyReviewBusy
                                    onClicked: {
                                        if (appController.refreshApiConfiguration()) {
                                            appController.generateStory(storyTargetDuration)
                                        } else {
                                            apiPreflightDialog.requestedAction = "story"
                                            apiPreflightDialog.open()
                                        }
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                visible: storySection.expanded && appController.seriesParts.length > 1
                                Layout.preferredHeight: visible ? seriesPartContent.implicitHeight + 24 : 0
                                radius: 11
                                color: "#181b23"
                                border.color: "#3c3155"

                                ColumnLayout {
                                    id: seriesPartContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 12
                                    spacing: 9
                                    Text {
                                        text: appController.seriesSummary
                                        color: "#cfc6e9"
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Repeater {
                                            model: appController.seriesParts
                                            delegate: Rectangle {
                                                required property var modelData
                                                width: Math.min(210, Math.max(86, partLabel.implicitWidth + 28))
                                                height: 34
                                                radius: 9
                                                color: modelData.current ? "#6d28d9" : "#252832"
                                                border.color: modelData.current ? accentLight : "#3a3e49"
                                                Text {
                                                    id: partLabel
                                                    anchors.centerIn: parent
                                                    width: Math.min(180, implicitWidth)
                                                    text: "第 " + modelData.partIndex + " 集 · " + modelData.title
                                                    color: modelData.current ? "white" : "#d3d6df"
                                                    font.pixelSize: 11
                                                    font.bold: modelData.current
                                                    elide: Text.ElideRight
                                                }
                                                MouseArea {
                                                    anchors.fill: parent
                                                    enabled: !modelData.current && !appController.storyBusy
                                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                                    onClicked: appController.openSeriesPart(modelData.partIndex)
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: storySection.expanded
                                spacing: 8
                                enabled: understandingDone && !appController.storyBusy
                                opacity: enabled ? 1 : 0.45
                                Text {
                                    text: manuscriptProject || appController.analysisContentMode === "visual" ? "期望时长" : "目标上限"
                                    color: textMuted
                                    font.pixelSize: 11
                                    Layout.rightMargin: 4
                                }
                                Repeater {
                                    model: [60, 90, 120, 180]
                                    delegate: Rectangle {
                                        required property int modelData
                                        Layout.preferredWidth: 66
                                        Layout.preferredHeight: 30
                                        radius: 8
                                        color: storyTargetDuration === modelData ? "#6d28d9" : "#252832"
                                        border.color: storyTargetDuration === modelData ? accentLight : "#343843"
                                        Text { anchors.centerIn: parent; text: modelData + " 秒"; color: storyTargetDuration === modelData ? "white" : "#c4c6cf"; font.pixelSize: 11 }
                                        MouseArea {
                                            anchors.fill: parent
                                            enabled: understandingDone && !appController.storyBusy
                                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                            onClicked: storyTargetDuration = modelData
                                        }
                                    }
                                }
                                Text {
                                    text: manuscriptProject
                                          ? "一次文本请求同时生成英文解说和镜头需求；真实配音后再按同步 SRT 校准镜头。"
                                          : appController.analysisContentMode === "visual"
                                          ? "长片会优先保证故事完整，允许超过期望值，但绝不会超过 Shorts 三分钟上限。"
                                          : "原片信息不足时会自动缩短，不会强行填满。"
                                    color: "#737986"
                                    font.pixelSize: 10
                                    Layout.fillWidth: true
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: storySection.expanded
                                spacing: 10
                                enabled: understandingDone && !appController.storyBusy
                                opacity: enabled ? 1 : 0.45

                                Text {
                                    text: "叙事策略"
                                    color: textMuted
                                    font.pixelSize: 11
                                    Layout.rightMargin: 4
                                }
                                ComboBox {
                                    id: narrativeStrategyCombo
                                    objectName: "narrativeStrategyCombo"
                                    Layout.preferredWidth: 190
                                    Layout.preferredHeight: 34
                                    model: appController.narrativeStrategyOptions
                                    textRole: "label"
                                    valueRole: "value"

                                    function syncSelection() {
                                        for (let i = 0; i < model.length; ++i) {
                                            if (String(model[i].value) === appController.narrativeStrategy) {
                                                currentIndex = i
                                                return
                                            }
                                        }
                                        currentIndex = 0
                                    }

                                    Component.onCompleted: syncSelection()
                                    onActivated: {
                                        if (currentIndex >= 0)
                                            appController.setNarrativeStrategy(String(model[currentIndex].value))
                                    }
                                    Connections {
                                        target: appController
                                        function onStoryChanged() { narrativeStrategyCombo.syncSelection() }
                                    }
                                    contentItem: Text {
                                        leftPadding: 11
                                        rightPadding: 30
                                        text: narrativeStrategyCombo.displayText
                                        color: textMain
                                        font.pixelSize: 11
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }
                                    indicator: Text {
                                        x: narrativeStrategyCombo.width - width - 11
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: narrativeStrategyCombo.popup.visible ? "▴" : "▾"
                                        color: accentLight
                                        font.pixelSize: 11
                                    }
                                    background: Rectangle {
                                        radius: 8
                                        color: narrativeStrategyCombo.hovered ? "#252936" : "#1d2029"
                                        border.color: narrativeStrategyCombo.activeFocus ? accent : "#383c48"
                                    }
                                    delegate: ItemDelegate {
                                        required property var modelData
                                        required property int index
                                        width: narrativeStrategyCombo.width
                                        height: 38
                                        highlighted: narrativeStrategyCombo.highlightedIndex === index
                                        contentItem: Text {
                                            text: modelData.label
                                            color: highlighted ? "white" : "#d3d6df"
                                            font.pixelSize: 11
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: highlighted ? "#553494" : "transparent"
                                        }
                                    }
                                    popup: Popup {
                                        y: narrativeStrategyCombo.height + 4
                                        width: narrativeStrategyCombo.width
                                        implicitHeight: Math.min(contentItem.implicitHeight + 8, 330)
                                        padding: 4
                                        contentItem: ListView {
                                            clip: true
                                            implicitHeight: contentHeight
                                            model: narrativeStrategyCombo.popup.visible
                                                   ? narrativeStrategyCombo.delegateModel : null
                                            currentIndex: narrativeStrategyCombo.highlightedIndex
                                            ScrollIndicator.vertical: ScrollIndicator { }
                                        }
                                        background: Rectangle {
                                            radius: 9
                                            color: "#1a1d25"
                                            border.color: "#3b4050"
                                        }
                                    }
                                }
                                Text {
                                    text: appController.narrativeStrategyHint
                                    color: "#858b99"
                                    font.pixelSize: 10
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                visible: storySection.expanded && (appController.storyBusy || appController.storyStatus.indexOf("故事生成失败") === 0 || appController.storyStatus.indexOf("分镜规划失败") === 0)
                                Layout.preferredHeight: visible ? 76 : 0
                                radius: 10
                                color: "#15171e"
                                border.color: appController.storyStatus.indexOf("失败") >= 0 ? "#7f3d46" : "#292c36"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 13
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 7
                                        Text {
                                            text: appController.storyStatus
                                            color: textMain
                                            font.pixelSize: 12
                                            Layout.fillWidth: true
                                            wrapMode: Text.WordWrap
                                        }
                                        Text {
                                            visible: appController.storyBusy
                                            text: appController.storyElapsedText + " · 正在等待模型返回，请勿重复点击"
                                            color: textMuted
                                            font.pixelSize: 10
                                        }
                                        Rectangle {
                                            id: storyProgressTrack
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 5
                                            radius: 3
                                            color: "#30333d"
                                            clip: true
                                            Rectangle {
                                                visible: !appController.storyBusy
                                                width: parent.width * appController.storyProgress
                                                height: parent.height
                                                radius: 3
                                                color: accent
                                            }
                                            Rectangle {
                                                id: storyProgressPulse
                                                visible: appController.storyBusy
                                                width: Math.max(48, parent.width * 0.24)
                                                height: parent.height
                                                radius: 3
                                                color: accent
                                                x: -width
                                                NumberAnimation on x {
                                                    from: -storyProgressPulse.width
                                                    to: storyProgressTrack.width
                                                    duration: 1250
                                                    loops: Animation.Infinite
                                                    running: appController.storyBusy
                                                }
                                            }
                                        }
                                    }
                                    LoadingRing {
                                        visible: appController.storyBusy
                                        running: visible
                                        ringColor: accentLight
                                        Layout.preferredWidth: 24
                                        Layout.preferredHeight: 24
                                    }
                                    Text {
                                        visible: !appController.storyBusy
                                        text: Math.round(appController.storyProgress * 100) + "%"
                                        color: accentLight
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                visible: storySection.expanded && storyDone
                                Layout.preferredHeight: visible ? combinedReviewContent.implicitHeight + 26 : 0
                                radius: 12
                                color: "#15171e"
                                border.color: appController.factReviewStatus.indexOf("高风险") >= 0 ? "#7f3d46" : "#303440"

                                ColumnLayout {
                                    id: combinedReviewContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 13
                                    spacing: 10

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            Text { text: "可选 · 文案审查"; color: textMain; font.pixelSize: 14; font.bold: true }
                                            Text {
                                                text: appController.contentReviewStatus
                                                color: appController.contentReviewIssues.length > 0 ? "#f1c978" : textMuted
                                                font.pixelSize: 11
                                                Layout.fillWidth: true
                                                wrapMode: Text.WordWrap
                                            }
                                        }
                                        GhostButton {
                                            visible: appController.contentReviewPendingCount > 1
                                            text: "应用全部建议"
                                            enabled: appController.contentReviewPendingCount > 0 && !appController.contentReviewBusy && !appController.storyBusy
                                            onClicked: appController.applyAllContentReviewSuggestions()
                                        }
                                        GhostButton {
                                            text: appController.contentReviewBusy ? "正在综合审查…" : "开始文案审查"
                                            enabled: !appController.contentReviewBusy && !appController.storyBusy
                                            onClicked: appController.runFactReview()
                                        }
                                    }

                                    ProgressBar {
                                        Layout.fillWidth: true
                                        visible: appController.contentReviewBusy
                                        indeterminate: true
                                    }

                                    Text {
                                        visible: appController.contentReviewSummary !== ""
                                        text: appController.contentReviewSummary
                                        color: textMain
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                                    Text {
                                        visible: appController.contentReviewBreakdown !== ""
                                        text: appController.contentReviewBreakdown
                                        color: "#9fc5a8"
                                        font.pixelSize: 10
                                        font.bold: true
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        visible: appController.contentReviewCanonicalTerms.length > 0
                                        Repeater {
                                            model: appController.contentReviewCanonicalTerms
                                            delegate: Rectangle {
                                                required property var modelData
                                                width: unifiedCanonicalText.implicitWidth + 18
                                                height: 26
                                                radius: 7
                                                color: "#202735"
                                                border.color: "#3b4c68"
                                                Text {
                                                    id: unifiedCanonicalText
                                                    anchors.centerIn: parent
                                                    text: (modelData.source_term ? modelData.source_term + " → " : "") + modelData.preferred_en
                                                    color: "#b9d4ff"
                                                    font.pixelSize: 9
                                                }
                                            }
                                        }
                                    }

                                    Repeater {
                                        model: appController.contentReviewIssues
                                        delegate: Rectangle {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 116
                                            radius: 9
                                            color: modelData.applied ? "#16251d"
                                                   : modelData.reviewType === "fact" && modelData.severity === "high" ? "#28191d"
                                                   : modelData.reviewType === "terminology" ? "#211e18" : "#1c1e26"
                                            border.color: modelData.applied ? "#347456"
                                                          : modelData.reviewType === "fact" && modelData.severity === "high" ? "#7f3d46"
                                                          : modelData.reviewType === "terminology" ? "#655431" : "#343843"
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 10
                                                spacing: 4
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    Rectangle {
                                                        Layout.preferredWidth: unifiedTypeText.implicitWidth + 14
                                                        Layout.preferredHeight: 22
                                                        radius: 6
                                                        color: modelData.reviewType === "fact" ? "#2b2145" : "#3a301e"
                                                        Text {
                                                            id: unifiedTypeText
                                                            anchors.centerIn: parent
                                                            text: modelData.reviewTypeText
                                                            color: modelData.reviewType === "fact" ? accentLight : "#f1c978"
                                                            font.pixelSize: 9
                                                            font.bold: true
                                                        }
                                                    }
                                                    Text { text: modelData.titleText; color: textMain; font.pixelSize: 10; font.bold: true }
                                                    Text { text: "解说句 " + modelData.narrationText; color: textMuted; font.pixelSize: 9 }
                                                    Item { Layout.fillWidth: true }
                                                    Button {
                                                        text: modelData.applied ? "✓ 已应用"
                                                              : modelData.suggestion_en ? "应用建议" : "手动处理"
                                                        implicitWidth: 72
                                                        implicitHeight: 25
                                                        enabled: !modelData.applied && modelData.suggestion_en !== "" && !appController.contentReviewBusy && !appController.storyBusy
                                                        contentItem: Text {
                                                            text: parent.text
                                                            color: modelData.applied ? "#91e5b4" : "#b8f3cf"
                                                            font.pixelSize: 9
                                                            horizontalAlignment: Text.AlignHCenter
                                                            verticalAlignment: Text.AlignVCenter
                                                        }
                                                        background: Rectangle {
                                                            radius: 6
                                                            color: modelData.applied ? "#183729" : parent.hovered ? "#224633" : "#183729"
                                                            border.color: "#347456"
                                                        }
                                                        onClicked: {
                                                            if (modelData.reviewType === "fact")
                                                                appController.applyFactReviewSuggestion(modelData.id)
                                                            else
                                                                appController.applyTerminologySuggestion(modelData.id)
                                                        }
                                                    }
                                                }
                                                Text { visible: modelData.subjectText !== ""; text: modelData.subjectText; color: modelData.reviewType === "terminology" ? "#d8c18f" : textMain; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                Text { text: modelData.reason_zh; color: "#b8bdc9"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                                Text { text: "建议：" + modelData.suggestion_en; color: "#8ee3b4"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                            }
                                        }
                                    }

                                    Text {
                                        text: appController.contentReviewDisclaimer
                                        color: "#737986"
                                        font.pixelSize: 9
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                            Rectangle {
                                visible: false
                                Layout.fillWidth: true
                                Layout.preferredHeight: factReviewContent.implicitHeight + 22
                                radius: 10
                                color: "#181b22"
                                border.color: "#303440"

                                ColumnLayout {
                                    id: factReviewContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 11
                                    spacing: 9

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            Text { text: "可选 · 文案审查"; color: textMain; font.pixelSize: 14; font.bold: true }
                                            Text {
                                                text: appController.factReviewStatus
                                                color: appController.factReviewStatus.indexOf("高风险") >= 0 ? "#ff9ba6" : textMuted
                                                font.pixelSize: 11
                                                Layout.fillWidth: true
                                                wrapMode: Text.WordWrap
                                            }
                                        }
                                        GhostButton {
                                            text: appController.factReviewBusy ? "正在综合审查…" : "开始文案审查"
                                            enabled: !appController.factReviewBusy && !appController.terminologyReviewBusy && !appController.storyBusy
                                            onClicked: appController.runFactReview()
                                        }
                                    }

                                    Text {
                                        visible: appController.factReviewSummary !== ""
                                        text: appController.factReviewSummary
                                        color: textMain
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                                    Repeater {
                                        model: appController.factReviewIssues
                                        delegate: Rectangle {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 116
                                            radius: 9
                                            color: modelData.severity === "high" ? "#28191d" : "#1c1e26"
                                            border.color: modelData.severity === "high" ? "#7f3d46" : "#343843"
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 10
                                                spacing: 4
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    Text { text: modelData.severityText + " · " + modelData.categoryText; color: modelData.severity === "high" ? "#ff9ba6" : accentLight; font.pixelSize: 10; font.bold: true }
                                                    Text { text: modelData.narrationText ? "解说句 " + modelData.narrationText : "全局提示"; color: textMuted; font.pixelSize: 9 }
                                                    Item { Layout.fillWidth: true }
                                                    Button {
                                                        visible: modelData.suggestion_en !== "" && modelData.narration_ids && modelData.narration_ids.length === 1
                                                        text: "应用建议"
                                                        implicitWidth: 72
                                                        implicitHeight: 25
                                                        contentItem: Text {
                                                            text: parent.text
                                                            color: "#b8f3cf"
                                                            font.pixelSize: 9
                                                            horizontalAlignment: Text.AlignHCenter
                                                            verticalAlignment: Text.AlignVCenter
                                                        }
                                                        background: Rectangle {
                                                            radius: 6
                                                            color: parent.hovered ? "#224633" : "#183729"
                                                            border.color: "#347456"
                                                        }
                                                        onClicked: appController.applyFactReviewSuggestion(modelData.id)
                                                    }
                                                }
                                                Text { text: modelData.claim_en; color: textMain; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                                Text { text: modelData.reason_zh; color: "#b8bdc9"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                                Text { visible: modelData.suggestion_en !== ""; text: "建议：" + modelData.suggestion_en; color: "#8ee3b4"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                            }
                                        }
                                    }

                                    Text {
                                        text: appController.factReviewDisclaimer
                                        color: "#737986"
                                        font.pixelSize: 9
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }

                            Rectangle {
                                visible: false
                                Layout.fillWidth: true
                                Layout.preferredHeight: terminologyReviewContent.implicitHeight + 22
                                radius: 10
                                color: "#181b22"
                                border.color: appController.terminologyReviewIssues.length > 0 ? "#6b5831" : "#303440"

                                ColumnLayout {
                                    id: terminologyReviewContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 13
                                    spacing: 9

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            Text { text: "术语、名称与单位"; color: textMain; font.pixelSize: 13; font.bold: true }
                                            Text {
                                                text: appController.terminologyReviewStatus
                                                color: appController.terminologyReviewIssues.length > 0 ? "#f1c978" : textMuted
                                                font.pixelSize: 11
                                                Layout.fillWidth: true
                                                wrapMode: Text.WordWrap
                                            }
                                        }
                                        GhostButton {
                                            visible: appController.terminologyReviewIssues.length > 1
                                            text: "应用全部建议"
                                            enabled: !appController.terminologyReviewBusy && !appController.storyBusy
                                            onClicked: appController.applyAllTerminologySuggestions()
                                        }
                                    }

                                    ProgressBar {
                                        Layout.fillWidth: true
                                        visible: appController.terminologyReviewBusy
                                        indeterminate: true
                                    }

                                    Text {
                                        visible: appController.terminologyReviewSummary !== ""
                                        text: appController.terminologyReviewSummary
                                        color: textMain
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        visible: appController.terminologyCanonicalTerms.length > 0
                                        Repeater {
                                            model: appController.terminologyCanonicalTerms
                                            delegate: Rectangle {
                                                required property var modelData
                                                width: canonicalTermText.implicitWidth + 18
                                                height: 26
                                                radius: 7
                                                color: "#202735"
                                                border.color: "#3b4c68"
                                                Text {
                                                    id: canonicalTermText
                                                    anchors.centerIn: parent
                                                    text: (modelData.source_term ? modelData.source_term + " → " : "") + modelData.preferred_en
                                                    color: "#b9d4ff"
                                                    font.pixelSize: 9
                                                }
                                            }
                                        }
                                    }

                                    Repeater {
                                        model: appController.terminologyReviewIssues
                                        delegate: Rectangle {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 112
                                            radius: 9
                                            color: "#211e18"
                                            border.color: "#655431"
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 10
                                                spacing: 4
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    Text { text: modelData.categoryText + (modelData.term ? " · " + modelData.term : ""); color: "#f1c978"; font.pixelSize: 10; font.bold: true }
                                                    Text { text: "解说句 " + modelData.narrationText; color: textMuted; font.pixelSize: 9 }
                                                    Item { Layout.fillWidth: true }
                                                    Button {
                                                        text: "应用建议"
                                                        implicitWidth: 72
                                                        implicitHeight: 25
                                                        contentItem: Text {
                                                            text: parent.text
                                                            color: "#b8f3cf"
                                                            font.pixelSize: 9
                                                            horizontalAlignment: Text.AlignHCenter
                                                            verticalAlignment: Text.AlignVCenter
                                                        }
                                                        background: Rectangle {
                                                            radius: 6
                                                            color: parent.hovered ? "#224633" : "#183729"
                                                            border.color: "#347456"
                                                        }
                                                        onClicked: appController.applyTerminologySuggestion(modelData.id)
                                                    }
                                                }
                                                Text { visible: modelData.variantsText !== ""; text: "发现：" + modelData.variantsText; color: "#d8c18f"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                Text { text: modelData.reason_zh; color: "#b8bdc9"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                                Text { text: "建议：" + modelData.suggestion_en; color: "#8ee3b4"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                            }
                                        }
                                    }

                                    Text {
                                        text: appController.terminologyReviewDisclaimer
                                        color: "#737986"
                                        font.pixelSize: 9
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                visible: storySection.expanded && storyDone

                                RowLayout {
                                    Layout.fillWidth: true
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Text { text: appController.storyTitle; color: textMain; font.pixelSize: 20; font.bold: true }
                                        Text { text: appController.storyAngle; color: textMuted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    }
                                    Rectangle {
                                        Layout.preferredWidth: 130
                                        Layout.preferredHeight: 30
                                        radius: 8
                                        color: "#252832"
                                        Text { anchors.centerIn: parent; text: appController.storyStats; color: accentLight; font.pixelSize: 11 }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 52
                                    visible: manuscriptProject
                                    radius: 10
                                    color: "#171a22"
                                    border.color: "#3d3155"
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 12
                                        Text { text: "分镜概览"; color: accentLight; font.pixelSize: 12; font.bold: true }
                                        Text { text: appController.storyboardSummary; color: "#d4d0df"; font.pixelSize: 11; Layout.fillWidth: true }
                                        Text { text: "绿色准确 · 黄色替代 · 红色缺失"; color: "#858b98"; font.pixelSize: 10 }
                                    }
                                }

                                Text { text: manuscriptProject ? "分镜需求列表" : "故事大纲"; color: textMain; font.pixelSize: 15; font.bold: true }
                                Repeater {
                                    model: manuscriptProject ? appController.storyboardBeats : appController.storyOutline
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: manuscriptProject ? 166 : 52
                                        radius: 10
                                        color: "#15171e"
                                        border.color: manuscriptProject ? "#5a3038" : "#292c36"
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 12; spacing: 12
                                            visible: !manuscriptProject
                                            Text { text: modelData.order; color: accentLight; font.pixelSize: 13; font.bold: true }
                                            Text { text: modelData.purpose; color: "#a1a6b2"; font.pixelSize: 11; Layout.preferredWidth: 70 }
                                            Text { text: modelData.summary; color: textMain; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                                            Text { text: "事件 " + modelData.event_ids.join(", "); color: textMuted; font.pixelSize: 10 }
                                        }
                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.margins: 13
                                            spacing: 13
                                            visible: manuscriptProject
                                            Rectangle {
                                                Layout.preferredWidth: 48
                                                Layout.preferredHeight: 48
                                                radius: 12
                                                color: "#34202a"
                                                border.color: "#70404b"
                                                Column {
                                                    anchors.centerIn: parent
                                                    spacing: 1
                                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "镜头"; color: "#c7a5ad"; font.pixelSize: 9 }
                                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.id; color: "#fecdd3"; font.pixelSize: 16; font.bold: true }
                                                }
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                spacing: 5
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    Text { text: modelData.text_en; color: textMain; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                                                    Rectangle {
                                                        Layout.preferredWidth: 66
                                                        Layout.preferredHeight: 24
                                                        radius: 7
                                                        color: "#40242c"
                                                        Text { anchors.centerIn: parent; text: "待补素材"; color: "#fda4af"; font.pixelSize: 9; font.bold: true }
                                                    }
                                                }
                                                Text { text: "理想画面：" + modelData.ideal_shot_zh; color: "#d7d9e0"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                                Text { text: "必须包含：" + (modelData.must_show_zh ? modelData.must_show_zh.join("、") : "未指定"); color: "#a9afbb"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                Text { text: "可接受替代：" + (modelData.acceptable_fallbacks_zh ? modelData.acceptable_fallbacks_zh.join("、") : "无"); color: "#a9afbb"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    Text { text: modelData.shot_role + " · " + modelData.media_kind + " · 约 " + modelData.estimated_duration_sec + " 秒"; color: accentLight; font.pixelSize: 10 }
                                                    Text { text: "搜索：" + (modelData.search_queries_en ? modelData.search_queries_en.join(" / ") : ""); color: "#777d89"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                }
                                            }
                                        }
                                    }
                                }

                                Text { text: "英文解说 · 点击文字可以修改，离开输入框自动保存"; color: textMain; font.pixelSize: 15; font.bold: true; Layout.topMargin: 4 }
                                Repeater {
                                    model: appController.storyNarration
                                    delegate: Rectangle {
                                        required property var modelData
                                        required property int index
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 112
                                        radius: 11
                                        color: "#15171e"
                                        border.color: narrationEditor.activeFocus ? accent : "#292c36"
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 12; spacing: 12
                                            Rectangle {
                                                Layout.preferredWidth: 42
                                                Layout.preferredHeight: 28
                                                radius: 8
                                                color: "#252832"
                                                Text { anchors.centerIn: parent; text: "句 " + modelData.id; color: "#b9a0df"; font.pixelSize: 10; font.bold: true }
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 5
                                                TextArea {
                                                    id: narrationEditor
                                                    Layout.fillWidth: true; Layout.fillHeight: true
                                                    text: modelData.text_en
                                                    color: textMain
                                                    font.pixelSize: 13
                                                    wrapMode: TextEdit.WordWrap
                                                    background: Rectangle { color: "transparent" }
                                                    onActiveFocusChanged: if (!activeFocus && text !== modelData.text_en) appController.updateNarration(index, text)
                                                }
                                                Text { text: "画面：" + modelData.visual_query + "  ·  约 " + modelData.estimated_duration_sec + " 秒  ·  事件 " + modelData.event_ids.join(", "); color: textMuted; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: matchingSection
                        property bool expanded: true
                        Layout.fillWidth: true
                        Layout.preferredHeight: expanded ? matchingContent.implicitHeight + 36 : 90
                        radius: 14
                        color: panel
                        border.color: matchingDone ? "#326b4d" : "#292c36"
                        ColumnLayout {
                            id: matchingContent
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 14
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16
                                StepBadge { stepNumber: "03"; completed: matchingDone }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Text { text: manuscriptProject ? "匹配素材" : "匹配镜头"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: appController.matches.length > 0
                                              ? appController.matchingStatus
                                              : manuscriptProject && appController.manuscriptAssetCount > 0
                                                ? appController.matchingStatus
                                              : appController.storyNarration.length > 0
                                                ? manuscriptProject
                                                  ? "分镜已经就绪；请选择你准备好的视频或图片素材。"
                                                  : "自动从全部场景中选取镜头；需要时可展开高级调整人工纠错。"
                                                : "完成故事与英文解说后，才能开始镜头匹配。"
                                        color: textMuted; font.pixelSize: 12
                                    }
                                    TapHandler { onTapped: matchingSection.expanded = !matchingSection.expanded }
                                }
                                GhostButton {
                                    text: matchingSection.expanded ? "收起  ▴" : "展开  ▾"
                                    onClicked: matchingSection.expanded = !matchingSection.expanded
                                }
                                GhostButton {
                                    visible: manuscriptProject
                                    text: "素材设置"
                                    onClicked: stockMediaSettingsDialog.open()
                                }
                                GhostButton {
                                    visible: manuscriptProject
                                    text: appController.manuscriptAssetCount > 0 ? "本地素材 " + appController.manuscriptAssetCount : "导入本地素材"
                                    enabled: storyDone && !appController.stockSearchBusy
                                    onClicked: manuscriptAssetsDialog.open()
                                }
                                FlatButton {
                                    text: appController.stockSearchBusy
                                          ? "正在寻找素材…"
                                          : appController.matchingBusy
                                          ? "正在自动匹配…"
                                          : appController.matches.length > 0
                                            ? "重新自动匹配"
                                            : manuscriptProject
                                              ? appController.stockSearchResults.length > 0 ? "重新寻找素材" : "自动寻找素材  →"
                                              : "自动匹配镜头  →"
                                    enabled: appController.storyNarration.length > 0 && !appController.matchingBusy && !appController.stockSearchBusy
                                    onClicked: {
                                        if (manuscriptProject) {
                                            if (appController.stockMediaConfigured)
                                                appController.searchStockMedia()
                                            else
                                                stockMediaSettingsDialog.open()
                                        } else {
                                            appController.generateMatches()
                                        }
                                    }
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                visible: matchingSection.expanded && manuscriptProject
                                Layout.preferredHeight: 58
                                radius: 10
                                color: "#11141a"
                                border.color: appController.stockSearchResults.length > 0 ? "#326b4d" : "#303440"
                                RowLayout {
                                    anchors.fill: parent; anchors.margins: 12; spacing: 10
                                    LoadingRing {
                                        visible: appController.stockSearchBusy
                                        running: visible
                                        ringColor: accentLight
                                        Layout.preferredWidth: 24; Layout.preferredHeight: 24
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 5
                                        Text {
                                            text: appController.stockSearchStatus
                                            color: appController.stockSearchResults.length > 0 ? "#8ee3b4" : textMuted
                                            font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight
                                        }
                                        Rectangle {
                                            visible: appController.stockSearchBusy
                                            Layout.fillWidth: true; Layout.preferredHeight: 4; radius: 2; color: "#30333d"
                                            Rectangle { width: parent.width * appController.stockSearchProgress; height: parent.height; radius: 2; color: accent }
                                        }
                                    }
                                    GhostButton {
                                        visible: appController.stockSearchResults.length > 0 && !appController.stockSearchBusy
                                        text: "查看候选"
                                        onClicked: stockResultsDialog.open()
                                    }
                                    Text {
                                        visible: appController.stockSearchBusy
                                        text: Math.round(appController.stockSearchProgress * 100) + "%"
                                        color: accentLight; font.pixelSize: 11; font.bold: true
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: matchingSection.expanded && appController.matches.length > 0
                                Text {
                                    Layout.fillWidth: true
                                    text: appController.roughCutSummary + " · 已自动完成，无需逐句选择"
                                    color: accentLight
                                    font.pixelSize: 11
                                }
                                GhostButton {
                                    text: matchingAdvancedVisible ? "隐藏高级调整" : "高级调整（可选）"
                                    onClicked: matchingAdvancedVisible = !matchingAdvancedVisible
                                }
                            }

                            Repeater {
                                model: appController.matches
                                visible: matchingSection.expanded && matchingAdvancedVisible
                                delegate: Rectangle {
                                    id: matchingItem
                                    required property var modelData
                                    visible: matchingSection.expanded && matchingAdvancedVisible
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: visible ? 292 : 0
                                    radius: 12
                                    color: "#15171e"
                                    border.color: "#292c36"
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 12
                                        spacing: 9
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Rectangle {
                                                width: 28; height: 28; radius: 8; color: "#292238"
                                                Text { anchors.centerIn: parent; text: modelData.narration_id; color: accentLight; font.bold: true; font.pixelSize: 11 }
                                            }
                                            Text { text: modelData.text_en; color: textMain; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                                            Text { text: "解说约 " + modelData.narration_duration_sec + " 秒"; color: textMuted; font.pixelSize: 10 }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 10
                                            Repeater {
                                                model: modelData.candidates
                                                delegate: Rectangle {
                                                    id: candidateItem
                                                    required property var modelData
                                                    Layout.fillWidth: true
                                                    Layout.preferredHeight: 142
                                                    radius: 9
                                                    color: modelData.event_id === matchingItem.modelData.selected_event_id ? "#272039" : "#20222a"
                                                    border.width: modelData.event_id === matchingItem.modelData.selected_event_id ? 2 : 1
                                                    border.color: modelData.event_id === matchingItem.modelData.selected_event_id ? accent : "#343843"
                                                    clip: true
                                                    ColumnLayout {
                                                        anchors.fill: parent
                                                        anchors.margins: 6
                                                        spacing: 4
                                                        Image {
                                                            Layout.fillWidth: true
                                                            Layout.fillHeight: true
                                                            source: modelData.keyframeUrl
                                                            fillMode: Image.PreserveAspectCrop
                                                            asynchronous: true
                                                        }
                                                        RowLayout {
                                                            Layout.fillWidth: true
                                                            Text { text: "场景 " + modelData.event_id + " · " + modelData.timeRange; color: textMain; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                                                            Text { text: modelData.scorePercent + "%"; color: accentLight; font.pixelSize: 9; font.bold: true }
                                                        }
                                                        Text { text: modelData.reason; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: appController.selectMatch(matchingItem.modelData.narration_id, candidateItem.modelData.event_id)
                                                        onDoubleClicked: {
                                                            appController.requestPreviewFrame(candidateItem.modelData.start)
                                                            previewDialog.open()
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 7
                                            Text {
                                                text: modelData.isCovered ? "时长已覆盖" : "时长不足"
                                                color: modelData.isCovered ? "#63d39a" : "#ff9c7a"
                                                font.pixelSize: 10
                                                font.bold: true
                                            }
                                            Text { text: modelData.coverageText + " · 当前 " + modelData.selectedRangeText; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true }
                                            GhostButton { text: "入点 -0.5"; onClicked: appController.adjustMatchBoundary(matchingItem.modelData.narration_id, "start", -0.5) }
                                            GhostButton { text: "入点 +0.5"; onClicked: appController.adjustMatchBoundary(matchingItem.modelData.narration_id, "start", 0.5) }
                                            GhostButton { text: "出点 -0.5"; onClicked: appController.adjustMatchBoundary(matchingItem.modelData.narration_id, "end", -0.5) }
                                            GhostButton { text: "出点 +0.5"; onClicked: appController.adjustMatchBoundary(matchingItem.modelData.narration_id, "end", 0.5) }
                                        }
                                        Text { text: "单击候选画面可替换，双击可打开原片预览；入点/出点调整会立即保存到粗剪时间线"; color: textMuted; font.pixelSize: 9 }
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        id: exportSection
                        property bool expanded: true
                        Layout.fillWidth: true
                        Layout.preferredHeight: expanded ? exportContent.implicitHeight + 36 : 90
                        radius: 14
                        color: panel
                        border.color: exportDone ? "#326b4d" : "#292c36"
                        ColumnLayout {
                            id: exportContent
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 12
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16
                                StepBadge { stepNumber: "04"; completed: exportDone }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Text { text: manuscriptProject ? "配音导出" : "预览导出"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: appController.matches.length > 0
                                              ? appController.exportLayoutHint
                                              : "完成镜头匹配后，才能生成粗剪预览。"
                                        color: textMuted; font.pixelSize: 12
                                    }
                                    TapHandler { onTapped: exportSection.expanded = !exportSection.expanded }
                                }
                                GhostButton {
                                    text: exportSection.expanded ? "收起  ▴" : "展开  ▾"
                                    onClicked: exportSection.expanded = !exportSection.expanded
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: exportSection.expanded && appController.matches.length > 0
                                spacing: 10

                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 70; radius: 11
                                    color: "#171a21"; border.color: "#303540"
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 12
                                        SubstepBadge { stepNumber: "1" }
                                        ColumnLayout { Layout.preferredWidth: 180; spacing: 2
                                            Text { text: "导出配音文稿"; color: textMain; font.pixelSize: 13; font.bold: true }
                                            Text { text: "提供给 GPT-SoVITS"; color: textMuted; font.pixelSize: 9 }
                                        }
                                        DarkComboBox { id: ttsDocumentFormatCombo; Layout.preferredWidth: 158; model: ["TXT · 一句一行", "SRT · 含时间轴"]; currentIndex: 0 }
                                        GhostButton {
                                            text: "选择位置并导出"
                                            onClicked: {
                                                saveTtsSrtDialog.exportFormat = ttsDocumentFormatCombo.currentIndex === 0 ? "txt" : "srt"
                                                saveTtsSrtDialog.currentFile = appController.prepareTtsSrtExportUrl(saveTtsSrtDialog.exportFormat)
                                                saveTtsSrtDialog.open()
                                            }
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: voiceDetailColumn.implicitHeight + 24
                                    radius: 11
                                    color: "#171a21"
                                    border.color: appController.narrationAudioReady ? "#315d48" : "#303540"
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 12
                                        SubstepBadge { stepNumber: "2"; Layout.alignment: Qt.AlignTop }
                                        ColumnLayout {
                                            id: voiceDetailColumn
                                            Layout.fillWidth: true; spacing: 8
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 9
                                                ColumnLayout { Layout.preferredWidth: 180; spacing: 2
                                                    Text { text: "导入配音与同步 SRT"; color: textMain; font.pixelSize: 13; font.bold: true }
                                                    Text { text: appController.voiceStatus; color: appController.narrationAudioReady ? "#63d39a" : textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                                                }
                                                GhostButton { text: appController.narrationAudioReady ? "重新导入英文配音" : "导入英文配音"; enabled: !appController.voiceBusy; onClicked: narrationAudioDialog.open() }
                                                GhostButton { text: appController.syncedSrtReady ? "重新导入同步 SRT" : "导入同步 SRT（可选）"; enabled: !appController.voiceBusy; onClicked: narrationSrtDialog.open() }
                                                Text { visible: appController.narrationAudioReady; text: "配音 " + appController.narrationDurationText; color: accentLight; font.pixelSize: 10; font.bold: true }
                                                Item { Layout.fillWidth: true }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 9
                                                visible: appController.narrationAudioReady && !appController.syncedSrtReady
                                                Text { text: "没有同步 SRT，可从配音识别生成（CPU 可能较慢）"; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true }
                                                GhostButton { text: appController.voiceBusy ? "正在识别…" : "从配音生成 SRT"; enabled: !appController.voiceBusy; onClicked: appController.generateNarrationSrtWithWhisper() }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 9
                                                visible: appController.narrationAudioReady
                                                Text { text: appController.narrationSpeedHint; color: appController.narrationOverShortsLimit ? "#f1b86b" : textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                                                FlatButton {
                                                    visible: appController.narrationOverShortsLimit
                                                    text: appController.voiceBusy || appController.durationRevisionBusy ? "正在处理…" : (appController.canAutoFitNarration ? "自动适配至 3 分钟" : "AI 精简到 3 分钟")
                                                    enabled: !appController.voiceBusy && !appController.durationRevisionBusy && (appController.canAutoFitNarration || appController.canReviseNarrationDuration)
                                                    onClicked: appController.canAutoFitNarration ? appController.autoFitNarrationToShorts() : appController.proposeNarrationDurationRevision()
                                                }
                                                GhostButton { visible: appController.narrationSpeed > 1.001; text: "恢复原速"; enabled: !appController.voiceBusy; onClicked: appController.restoreNarrationSpeed() }
                                                GhostButton { visible: appController.canRestoreDurationRevision; text: "恢复精简前版本"; enabled: !appController.voiceBusy && !appController.durationRevisionBusy; onClicked: restoreRevisionDialog.open() }
                                            }
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: cleanupDetailColumn.implicitHeight + 24; radius: 11
                                    color: "#171a21"; border.color: appController.subtitleCleanedVideoReady ? "#315d48" : "#303540"
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 12
                                        SubstepBadge { stepNumber: "3"; Layout.alignment: Qt.AlignTop }
                                        ColumnLayout { id: cleanupDetailColumn; Layout.fillWidth: true; spacing: 7
                                            RowLayout { Layout.fillWidth: true; spacing: 9
                                                ColumnLayout { Layout.fillWidth: true; spacing: 2
                                                    Text { text: "清除原视频字幕"; color: textMain; font.pixelSize: 13; font.bold: true }
                                                    Text { text: "先设置遮罩区域，再生成后续步骤使用的去字幕中间视频"; color: textMuted; font.pixelSize: 9 }
                                                }
                                                GhostButton { text: "设置字幕遮罩"; enabled: !appController.subtitleCleanupBusy; onClicked: { appController.clearSubtitleEffectPreview(); subtitleStyleDialog.editMode = "cleanup"; subtitleStyleDialog.open() } }
                                                FlatButton { text: appController.subtitleCleanupBusy ? "正在生成…" : appController.subtitleCleanedVideoReady ? "重新生成去字幕视频" : "生成去字幕视频"; enabled: !appController.subtitleCleanupBusy && !appController.exportBusy; onClicked: appController.generateSubtitleCleanVideo() }
                                            }
                                            RowLayout { Layout.fillWidth: true
                                                Text { text: appController.subtitleCleanupStatus; color: appController.subtitleCleanedVideoReady ? "#63d39a" : textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                                                GhostButton { visible: appController.subtitleCleanedVideoReady; text: "用播放器打开"; onClicked: appController.openSubtitleCleanedVideo() }
                                                GhostButton { visible: appController.subtitleCleanedVideoReady; text: "删除中间视频"; foreground: "#fca5a5"; onClicked: deleteSubtitleCleanedVideoDialog.open() }
                                                Text { visible: appController.subtitleCleanupBusy; text: Math.round(appController.subtitleCleanupProgress * 100) + "%"; color: accentLight; font.pixelSize: 9; font.bold: true }
                                            }
                                            Rectangle { visible: appController.subtitleCleanupBusy; Layout.fillWidth: true; Layout.preferredHeight: 4; radius: 2; color: "#30333d"; Rectangle { width: parent.width * appController.subtitleCleanupProgress; height: parent.height; radius: 2; color: accent } }
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 108
                                    radius: 11; color: "#171a21"; border.color: "#303540"
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 8
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 12
                                            SubstepBadge { stepNumber: "4" }
                                            ColumnLayout { Layout.preferredWidth: 142; spacing: 2
                                                Text { text: "成片画布"; color: textMain; font.pixelSize: 13; font.bold: true }
                                                Text { text: "输出 " + appController.subtitleCanvasWidth + "×" + appController.subtitleCanvasHeight; color: textMuted; font.pixelSize: 9 }
                                            }
                                            Repeater {
                                                model: [
                                                    { key: "crop_stretch", title: "裁剪拉伸", note: "自由调整画面" },
                                                    { key: "vertical_blur", title: "竖屏完整", note: "不裁切主体" },
                                                    { key: "original", title: "原始比例", note: "不转换画布" }
                                                ]
                                                delegate: Rectangle {
                                                    required property var modelData
                                                    Layout.fillWidth: true; Layout.preferredHeight: 52; radius: 9
                                                    color: appController.exportFitMode === modelData.key ? "#332253" : "#1c2029"
                                                    border.width: appController.exportFitMode === modelData.key ? 2 : 1
                                                    border.color: appController.exportFitMode === modelData.key ? accent : "#343946"
                                                    Column { anchors.centerIn: parent; spacing: 2
                                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.title; color: textMain; font.pixelSize: 11; font.bold: true }
                                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.note; color: appController.exportFitMode === modelData.key ? accentLight : textMuted; font.pixelSize: 9 }
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: {
                                                            appController.setExportFitMode(modelData.key)
                                                            if (modelData.key === "crop_stretch")
                                                                canvasTransformDialog.open()
                                                        }
                                                    }
                                                }
                                            }
                                            GhostButton {
                                                visible: appController.exportFitMode === "crop_stretch"
                                                text: "调整"
                                                onClicked: canvasTransformDialog.open()
                                            }
                                        }
                                    }
                                }

                                Rectangle {
                                    visible: appController.burnSubtitles
                                    Layout.fillWidth: true; Layout.preferredHeight: 70; radius: 11
                                    color: "#171a21"; border.color: "#303540"
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 12
                                        SubstepBadge { stepNumber: "5" }
                                        ColumnLayout { Layout.fillWidth: true; spacing: 2
                                            Text { text: "字幕样式"; color: textMain; font.pixelSize: 13; font.bold: true }
                                            Text { text: "调整字体、字号、位置、描边和字幕安全区域"; color: textMuted; font.pixelSize: 9 }
                                        }
                                        GhostButton {
                                            text: "编辑字幕样式"
                                            enabled: appController.storyNarration.length > 0
                                            onClicked: {
                                                appController.clearSubtitleEffectPreview()
                                                subtitleStyleDialog.editMode = "style"
                                                subtitleStyleDialog.open()
                                                appController.generateSubtitleEffectPreview()
                                            }
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: synthesisColumn.implicitHeight + 24
                                    radius: 11; color: "#171a21"
                                    border.color: appController.previewVideoReady ? "#315d48" : "#303540"
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 12; spacing: 12
                                        SubstepBadge { stepNumber: appController.burnSubtitles ? "6" : "5"; Layout.alignment: Qt.AlignTop }
                                        ColumnLayout {
                                            id: synthesisColumn
                                            Layout.fillWidth: true; spacing: 9
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 8
                                                ColumnLayout { Layout.preferredWidth: 150; spacing: 2
                                                    Text { text: "合成与测试"; color: textMain; font.pixelSize: 13; font.bold: true }
                                                    Text { text: "确认设置后输出成片"; color: textMuted; font.pixelSize: 9 }
                                                }
                                                FlatButton { text: appController.exportBusy ? "正在生成…" : appController.previewVideoReady ? "重新生成成片预览" : "生成成片预览  →"; enabled: appController.narrationAudioReady && !appController.exportBusy && !appController.qualityCheckBusy; onClicked: appController.generateRoughPreview() }
                                                GhostButton { visible: appController.burnSubtitles; text: "仅字幕测试预览"; enabled: !appController.exportBusy && !appController.qualityCheckBusy; onClicked: appController.generateSubtitleOnlyPreview() }
                                                GhostButton { text: appController.qualityCheckBusy ? "检查中…" : "成片检查"; enabled: !appController.exportBusy && !appController.qualityCheckBusy; onClicked: appController.runQualityCheck() }
                                                Item { Layout.fillWidth: true }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 9
                                                Text { text: "是否烧录字幕"; color: textMain; font.pixelSize: 10; font.bold: true }
                                                ModernSwitch { checked: appController.burnSubtitles; onToggled: appController.setBurnSubtitles(checked) }
                                                Text { text: appController.burnSubtitles ? "显示第 5 步并应用全部字幕样式" : "不添加字幕，可在外部软件处理"; color: textMuted; font.pixelSize: 9 }
                                                Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 20; color: "#343946" }
                                                Text { text: "保留原片声音"; color: textMain; font.pixelSize: 10; font.bold: true }
                                                ModernSwitch { checked: appController.preserveOriginalAudio; onToggled: appController.setPreserveOriginalAudio(checked) }
                                                Text { text: appController.preserveOriginalAudio ? "原声将降低音量后混合" : "默认关闭"; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true }
                                                GhostButton { visible: appController.previewVideoReady; text: "用播放器打开"; onClicked: appController.openRoughPreview() }
                                                FlatButton {
                                                    visible: appController.previewVideoReady
                                                    text: "导出成片"
                                                    onClicked: {
                                                        saveFinalVideoDialog.currentFile = appController.prepareFinalVideoExportUrl()
                                                        saveFinalVideoDialog.open()
                                                    }
                                                }
                                            }
                                            RowLayout { Layout.fillWidth: true
                                                Text { text: appController.exportStatus; color: appController.previewVideoReady ? "#63d39a" : textMain; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                                                Text { text: Math.round(appController.exportProgress * 100) + "%"; color: accentLight; font.pixelSize: 10; font.bold: true }
                                            }
                                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 5; radius: 3; color: "#30333d"; Rectangle { width: parent.width * appController.exportProgress; height: parent.height; radius: 3; color: accent } }
                                            Text { visible: appController.previewVideoReady; text: appController.previewVideoPath; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 8
                        visible: understandingSection.expanded && appController.events.length > 0
                        Text { text: "原片事件"; color: textMain; font.pixelSize: 18; font.bold: true }
                        Text { text: appController.events.length + " 个场景事件"; color: textMuted; font.pixelSize: 12 }
                        Item { Layout.fillWidth: true }
                        GhostButton { text: "查看前 12 个"; enabled: false }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 14
                        rowSpacing: 12
                        visible: understandingSection.expanded && appController.events.length > 0

                        Repeater {
                            model: appController.events.slice(0, 12)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: modelData.technicalVisualSummary ? 154 : 136
                                radius: 13
                                color: panel
                                border.color: "#292c36"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 13
                                    Rectangle {
                                        Layout.preferredWidth: 148
                                        Layout.fillHeight: true
                                        radius: 9
                                        color: "#090a0e"
                                        clip: true
                                        Image { anchors.fill: parent; source: modelData.keyframeUrl; fillMode: Image.PreserveAspectCrop; asynchronous: true }
                                        Text { anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 7; text: modelData.timeRange; color: "white"; font.pixelSize: 10; style: Text.Outline; styleColor: "#80000000" }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        spacing: 6
                                        Text { text: "场景 " + modelData.id; color: accentLight; font.pixelSize: 11; font.bold: true }
                                        Text { Layout.fillWidth: true; text: modelData.visualDescription; color: textMain; font.pixelSize: 12; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                        Text {
                                            Layout.fillWidth: true
                                            visible: !!modelData.technicalVisualSummary
                                            text: "画面信息 · " + modelData.technicalVisualSummary + (modelData.highDetailReviewed ? "  ✓ 高清复查" : "")
                                            color: "#e8c76a"
                                            font.pixelSize: 10
                                            wrapMode: Text.WordWrap
                                            maximumLineCount: 2
                                            elide: Text.ElideRight
                                        }
                                        Text { Layout.fillWidth: true; Layout.fillHeight: true; text: modelData.transcript || "（该场景没有对白）"; color: textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                                    }
                                }
                            }
                        }
                    }

                }
            }
        }
    }
}
