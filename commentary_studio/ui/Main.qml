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
    title: "StoryCut Studio v" + appController.appVersion + " · AI 解说剪辑"
    color: "#0b0c10"

    property color accent: "#8b5cf6"
    property color accentLight: "#a78bfa"
    property color panel: "#15171e"
    property color panelRaised: "#1c1f28"
    property color textMain: "#f4f4f5"
    property color textMuted: "#9ca3af"
    property int currentStep: 0
    property int storyTargetDuration: 60
    property bool understandingDone: appController.events.length > 0
    property bool storyDone: appController.storyNarration.length > 0
    property bool matchingDone: appController.matches.length > 0
    property bool exportDone: appController.previewVideoReady

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

    font.family: "Microsoft YaHei UI"

    FileDialog {
        id: videoDialog
        title: "选择原始视频"
        nameFilters: ["视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v)", "所有文件 (*)"]
        onAccepted: appController.importVideo(selectedFile.toString())
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
            appController.openProject(selectedFile.toString())
            recentProjectsDialog.close()
        }
    }

    Dialog {
        id: recentProjectsDialog
        objectName: "recentProjectsDialog"
        width: Math.min(window.width - 80, 780)
        height: Math.min(
                    window.height - 70,
                    Math.max(430, 260 + Math.min(appController.recentProjects.length, 4) * 102)
                )
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
                GhostButton { text: "关闭"; onClicked: recentProjectsDialog.close() }
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
                                text: modelData.stageText + (modelData.updatedText ? "  ·  " + modelData.updatedText : "")
                                color: "#8b91a0"
                                font.pixelSize: 10
                            }
                        }

                        GhostButton {
                            text: "继续"
                            onClicked: {
                                appController.openProject(modelData.projectFile)
                                recentProjectsDialog.close()
                            }
                        }

                        Button {
                            id: deleteRecentButton
                            text: "删除"
                            implicitWidth: 58
                            implicitHeight: 36
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
                                deleteProjectDialog.open()
                            }
                        }
                    }

                    MouseArea {
                        id: projectRowMouse
                        anchors.fill: parent
                        anchors.rightMargin: 150
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            appController.openProject(modelData.projectFile)
                            recentProjectsDialog.close()
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
                text: "将永久删除“" + deleteProjectDialog.projectName + "”的项目文件、分析结果、配音、预览和导出缓存。"
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

    FileDialog {
        id: saveTtsSrtDialog
        title: "导出 SRT 到 GPT-SoVITS · 选择保存位置"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "srt"
        nameFilters: ["SRT 字幕 (*.srt)"]
        onAccepted: appController.saveTtsSrt(selectedFile.toString())
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
        property string requestedAction: "settings"
        width: 560
        anchors.centerIn: parent
        modal: true
        padding: 22
        closePolicy: Popup.CloseOnEscape

        function saveAndContinue() {
            if (!appController.saveApiConfiguration(apiKeyInput.text, apiBaseUrlInput.text))
                return
            close()
            if (requestedAction === "understanding")
                appController.startUnderstanding()
            else if (requestedAction === "story")
                appController.generateStory(storyTargetDuration)
        }

        background: Rectangle {
            radius: 16
            color: "#15171e"
            border.color: "#3a3f4c"
        }

        contentItem: ColumnLayout {
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

        onOpened: {
            apiBaseUrlInput.text = appController.apiBaseUrl
            apiKeyInput.text = appController.apiKey
            showApiKey.checked = false
            apiKeyInput.forceActiveFocus()
        }
        onClosed: apiKeyInput.text = ""
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
                text: "更新仅替换 commentary_studio 程序文件，不会修改项目、config.user.yaml、共享 .env 或模型。"
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
    }

    Dialog {
        id: subtitleStyleDialog
        objectName: "subtitleStyleDialog"
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
                        Text { anchors.centerIn: parent; text: "Aa"; color: "#c4b5fd"; font.pixelSize: 17; font.bold: true }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text { text: "字幕样式与位置"; color: textMain; font.pixelSize: 22; font.bold: true }
                        Text { text: "用同一块字幕底板遮住原字幕并承载英文字幕，所有设置自动保存到当前项目"; color: textMuted; font.pixelSize: 11 }
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
                        GhostButton {
                            text: appController.subtitleEffectPreviewBusy ? "生成中…" : "查看真实预览"
                            enabled: !appController.subtitleEffectPreviewBusy
                            onClicked: appController.generateSubtitleEffectPreview()
                        }
                        Text { text: appController.resolutionText; color: textMuted; font.pixelSize: 10 }
                    }
                    Rectangle {
                        id: subtitlePreviewFrame
                        Layout.fillWidth: true
                        Layout.preferredHeight: width * 9 / 16
                        Layout.alignment: Qt.AlignTop
                        radius: 13
                        color: "#05060a"
                        clip: true
                        Image {
                            id: subtitleEffectImage
                            anchors.fill: parent
                            source: appController.subtitleEffectPreviewUrl
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }
                        Rectangle {
                            visible: appController.subtitleStyle.cleanupMode !== "none"
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
                                    pressPoint = mapToItem(subtitlePreviewFrame, mouse.x, mouse.y)
                                    startX = appController.subtitleStyle.cleanupX
                                    startY = appController.subtitleStyle.cleanupY
                                    lockedAxis = ""
                                }
                                onPositionChanged: function(mouse) {
                                    if (!pressed)
                                        return
                                    var current = mapToItem(subtitlePreviewFrame, mouse.x, mouse.y)
                                    var dx = current.x - pressPoint.x
                                    var dy = current.y - pressPoint.y
                                    if (lockedAxis === "") {
                                        if (Math.max(Math.abs(dx), Math.abs(dy)) < 7)
                                            return
                                        lockedAxis = Math.abs(dx) > Math.abs(dy) * 1.15 ? "x" : "y"
                                    }
                                    if (lockedAxis === "x") {
                                        var nextX = Math.max(0, Math.min(1 - appController.subtitleStyle.cleanupWidth,
                                                                        startX + dx / subtitlePreviewFrame.width))
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
                                                                        startY + dy / subtitlePreviewFrame.height))
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
                        Rectangle {
                            id: subtitlePreviewBubble
                            width: parent.width * Math.max(0.55, 1 - appController.subtitleStyle.horizontalMargin * 2 / Math.max(1, appController.sourceVideoHeight * 16 / 9))
                            height: subtitlePreviewLabel.implicitHeight + 18
                            x: (parent.width - width) / 2
                            y: Math.max(8, parent.height - height - appController.subtitleStyle.bottomMargin / Math.max(1, appController.sourceVideoHeight) * parent.height)
                            radius: appController.subtitleStyle.backgroundEnabled ? 7 : 0
                            color: appController.subtitleStyle.backgroundEnabled
                                   ? Qt.rgba(0, 0, 0, appController.subtitleStyle.backgroundOpacity)
                                   : "transparent"
                            Text {
                                id: subtitlePreviewLabel
                                width: parent.width - 24
                                anchors.centerIn: parent
                                text: appController.subtitlePreviewText
                                color: "white"
                                font.family: appController.subtitleStyle.fontFamily
                                font.pixelSize: Math.max(10, appController.subtitleStyle.fontSize / Math.max(1, appController.sourceVideoHeight) * subtitlePreviewFrame.height)
                                font.bold: appController.subtitleStyle.bold
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                style: Text.Outline
                                styleColor: "#dd000000"
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
                                text: "字幕位置以原视频高度计算。当前默认不缩放、不补边、不裁切画面。"
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
                        id: subtitleSettingsScroll
                        anchors.fill: parent
                        anchors.margins: 16
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: subtitleSettingsScroll.availableWidth
                            spacing: 13
                            Text { text: "字幕底板类型"; color: textMain; font.pixelSize: 13; font.bold: true }
                            Text {
                                text: "这一块底板同时遮住原字幕并衬托英文字幕，不会再叠加第二层字幕背景。"
                                color: textMuted
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            ComboBox {
                                Layout.fillWidth: true
                                model: ["黑色遮罩（最彻底）", "局部柔化 / 模糊（推荐）", "Delogo 周边像素修复"]
                                currentIndex: appController.subtitleStyle.cleanupMode === "blur" ? 1
                                              : appController.subtitleStyle.cleanupMode === "delogo" ? 2 : 0
                                onActivated: appController.updateSubtitleStyle(
                                                 "cleanupMode",
                                                 currentIndex === 1 ? "blur" : currentIndex === 2 ? "delogo" : "mask")
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#2b2f39"; Layout.topMargin: 2; Layout.bottomMargin: 2 }
                            Text { text: "推荐预设"; color: textMain; font.pixelSize: 13; font.bold: true }
                            RowLayout {
                                Layout.fillWidth: true
                                GhostButton { text: "标准字幕"; onClicked: appController.applySubtitlePreset("box") }
                                GhostButton { text: "清爽描边"; onClicked: appController.applySubtitlePreset("outline") }
                                GhostButton { text: "Shorts 大字"; onClicked: appController.applySubtitlePreset("shorts") }
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
                                    Layout.fillWidth: true; from: 20; to: Math.max(120, appController.sourceVideoHeight * 0.42); stepSize: 2
                                    value: appController.subtitleStyle.bottomMargin
                                    onMoved: appController.updateSubtitleStyle("bottomMargin", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 20; to: Math.max(120, appController.sourceVideoHeight * 0.42)
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
                                Text { text: "粗体"; color: textMain; font.pixelSize: 11; Layout.fillWidth: true }
                                Switch {
                                    checked: appController.subtitleStyle.bold
                                    onToggled: appController.updateSubtitleStyle("bold", checked)
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#2b2f39"; Layout.topMargin: 2; Layout.bottomMargin: 2 }
                            Text { text: "底板区域"; color: textMain; font.pixelSize: 13; font.bold: true }
                            Text { text: "旧项目同款区域参数：先框准原字幕，再调整对应效果。"; color: textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "底板左侧"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0; to: 0.8; stepSize: 0.005
                                    value: appController.subtitleStyle.cleanupX
                                    onMoved: appController.updateSubtitleStyle("cleanupX", value)
                                }
                                PreciseSpinBox {
                                    from: 0; to: 160; divisor: 2; suffix: "%"
                                    value: Math.round(appController.subtitleStyle.cleanupX * 200)
                                    onValueModified: appController.updateSubtitleStyle("cleanupX", value / 200)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "底板顶部"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0.55; to: 0.94; stepSize: 0.005
                                    value: appController.subtitleStyle.cleanupY
                                    onMoved: appController.updateSubtitleStyle("cleanupY", value)
                                }
                                PreciseSpinBox {
                                    from: 110; to: 188; divisor: 2; suffix: "%"
                                    value: Math.round(appController.subtitleStyle.cleanupY * 200)
                                    onValueModified: appController.updateSubtitleStyle("cleanupY", value / 200)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "底板宽度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0.1; to: 1.0; stepSize: 0.005
                                    value: appController.subtitleStyle.cleanupWidth
                                    onMoved: appController.updateSubtitleStyle("cleanupWidth", value)
                                }
                                PreciseSpinBox {
                                    from: 20; to: 200; divisor: 2; suffix: "%"
                                    value: Math.round(appController.subtitleStyle.cleanupWidth * 200)
                                    onValueModified: appController.updateSubtitleStyle("cleanupWidth", value / 200)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "底板高度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0.04; to: 0.3; stepSize: 0.005
                                    value: appController.subtitleStyle.cleanupHeight
                                    onMoved: appController.updateSubtitleStyle("cleanupHeight", value)
                                }
                                PreciseSpinBox {
                                    from: 8; to: 60; divisor: 2; suffix: "%"
                                    value: Math.round(appController.subtitleStyle.cleanupHeight * 200)
                                    onValueModified: appController.updateSubtitleStyle("cleanupHeight", value / 200)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.cleanupMode === "mask"
                                Text { text: "底板浓度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0.25; to: 1.0; stepSize: 0.01
                                    value: appController.subtitleStyle.cleanupOpacity
                                    onMoved: appController.updateSubtitleStyle("cleanupOpacity", value)
                                }
                                PreciseSpinBox {
                                    from: 25; to: 100; value: Math.round(appController.subtitleStyle.cleanupOpacity * 100); suffix: "%"
                                    onValueModified: appController.updateSubtitleStyle("cleanupOpacity", value / 100)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.cleanupMode === "blur"
                                Text { text: "柔化强度"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 1; to: 40; stepSize: 1
                                    value: appController.subtitleStyle.blurRadius
                                    onMoved: appController.updateSubtitleStyle("blurRadius", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 1; to: 40; value: appController.subtitleStyle.blurRadius
                                    onValueModified: appController.updateSubtitleStyle("blurRadius", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.cleanupMode === "blur"
                                Text { text: "柔化层次"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 1; to: 4; stepSize: 1
                                    value: appController.subtitleStyle.blurPower
                                    onMoved: appController.updateSubtitleStyle("blurPower", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 1; to: 4; value: appController.subtitleStyle.blurPower
                                    onValueModified: appController.updateSubtitleStyle("blurPower", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.cleanupMode === "blur"
                                         || appController.subtitleStyle.cleanupMode === "delogo"
                                Text { text: "向外扩展"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0; to: 80; stepSize: 1
                                    value: appController.subtitleStyle.regionPadding
                                    onMoved: appController.updateSubtitleStyle("regionPadding", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 0; to: 80; value: appController.subtitleStyle.regionPadding; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("regionPadding", value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.subtitleStyle.cleanupMode === "blur"
                                Text { text: "柔边扩展"; color: textMain; font.pixelSize: 11; Layout.preferredWidth: 76 }
                                Slider {
                                    Layout.fillWidth: true; from: 0; to: 60; stepSize: 1
                                    value: appController.subtitleStyle.feather
                                    onMoved: appController.updateSubtitleStyle("feather", Math.round(value))
                                }
                                PreciseSpinBox {
                                    from: 0; to: 60; value: appController.subtitleStyle.feather; suffix: " px"
                                    onValueModified: appController.updateSubtitleStyle("feather", value)
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
                        { icon: "✦", label: "理解原片" },
                        { icon: "☷", label: "组织故事" },
                        { icon: "◫", label: "镜头匹配" },
                        { icon: "⇧", label: "导出成片" }
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
                                                || (index === 1 && appController.videoPath !== "")
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
                Text { text: "StoryCut Studio  v" + appController.appVersion; color: "#626773"; font.pixelSize: 11; Layout.topMargin: 12 }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: window.color

            ScrollView {
                id: mainScroll
                anchors.fill: parent
                contentWidth: availableWidth
                contentHeight: mainContent.implicitHeight + mainContent.y + 30

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
                            Text { text: "下午好，开始一个新故事"; color: textMain; font.pixelSize: 27; font.weight: Font.DemiBold }
                            Text { text: appController.notice; color: textMuted; font.pixelSize: 14 }
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
                        GhostButton { text: "最近项目"; onClicked: recentProjectsDialog.open() }
                        FlatButton { text: "+  创建项目"; onClicked: videoDialog.open() }
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
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: appController.mediaBusy ? "◌" : appController.videoPath ? "▶" : "＋"; color: accentLight; font.pixelSize: 34 }
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: appController.mediaBusy ? "正在生成封面…" : appController.videoPath ? "视频已导入" : "拖入或选择一个长视频"; color: textMain; font.pixelSize: 14; font.bold: true }
                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: appController.videoPath ? appController.durationText + "  ·  " + appController.resolutionText : "MP4 · MKV · MOV · WEBM"; color: "#d1d5db"; font.pixelSize: 11 }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: appController.videoPath ? previewDialog.open() : videoDialog.open()
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
                                    text: appController.videoPath || "导入后，程序会识别语音、检测场景，并把长视频整理成可用于解说的事件列表。"
                                    color: textMuted
                                    font.pixelSize: 13
                                    wrapMode: Text.WrapAnywhere
                                    maximumLineCount: 2
                                    elide: Text.ElideMiddle
                                }
                                Item { Layout.fillHeight: true }
                                RowLayout {
                                    spacing: 10
                                    Rectangle { width: 82; height: 27; radius: 7; color: "#20242c"; Text { anchors.centerIn: parent; text: appController.durationText; color: "#c7cad2"; font.pixelSize: 11 } }
                                    Rectangle { width: 96; height: 27; radius: 7; color: "#20242c"; Text { anchors.centerIn: parent; text: appController.resolutionText; color: "#c7cad2"; font.pixelSize: 11 } }
                                    Rectangle { width: 116; height: 27; radius: 7; color: "#20242c"; visible: appController.codecText !== ""; Text { anchors.centerIn: parent; text: appController.codecText; color: "#c7cad2"; font.pixelSize: 10 } }
                                    Item { Layout.fillWidth: true }
                                    FlatButton {
                                        text: appController.mediaBusy ? "正在读取…" : appController.videoPath ? "更换视频" : "选择视频  →"
                                        enabled: !appController.mediaBusy
                                        onClicked: videoDialog.open()
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
                                { n: "01", title: "理解原片", desc: "语音识别、场景检测与画面理解", state: appController.events.length > 0 ? "已完成" : appController.videoPath ? "可开始" : "等待视频" },
                                { n: "02", title: "组织故事", desc: "挑选关键事件，生成精简叙事", state: appController.storyNarration.length > 0 ? "已完成" : appController.events.length > 0 ? "可开始" : "等待理解原片" },
                                { n: "03", title: "匹配镜头", desc: "为每句解说寻找最佳原片段", state: appController.matches.length > 0 ? "已完成" : appController.storyNarration.length > 0 ? "可开始" : "等待组织故事" },
                                { n: "04", title: "预览导出", desc: "确认配音、字幕与镜头并输出成片", state: appController.previewVideoReady ? "预览已生成" : appController.matches.length > 0 ? "可开始" : "等待镜头匹配" }
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
                        Layout.fillWidth: true
                        Layout.preferredHeight: understandingContent.implicitHeight + 36
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
                                    Text { text: "理解原片"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: understandingDone
                                              ? appController.analysisStatus
                                              : appController.videoPath
                                                ? "识别语音、检测场景并理解关键画面。"
                                                : "请先在上方选择视频，随后即可开始原片理解。"
                                        color: understandingDone ? "#8ee3b4" : textMuted
                                        font.pixelSize: 12
                                    }
                                }
                                FlatButton {
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

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: appController.analysisBusy || appController.analysisProgress > 0
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: appController.analysisElapsedText; color: textMuted; font.pixelSize: 11 }
                                    Text { text: "·"; color: "#575b66"; font.pixelSize: 11 }
                                    Text { text: appController.analysisEstimatedTotalText; color: textMuted; font.pixelSize: 11 }
                                    Text { text: "·"; color: "#575b66"; font.pixelSize: 11 }
                                    Text { text: appController.analysisEtaText; color: accentLight; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Text { text: Math.round(appController.analysisProgress * 100) + "%"; color: understandingDone ? "#8ee3b4" : accentLight; font.pixelSize: 12; font.bold: true }
                                }
                                Text {
                                    visible: appController.analysisBusy
                                    text: "预计用时按低配电脑 CPU 算力估算，准确时间请以实际运行进度为准。"
                                    color: "#717784"
                                    font.pixelSize: 10
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
                        Layout.fillWidth: true
                        Layout.preferredHeight: storyContent.implicitHeight + 36
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
                                    Text { text: "组织故事"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: storyDone
                                              ? appController.storyStatus
                                              : understandingDone
                                                ? "选择目标上限，生成自然英文解说与故事结构。"
                                                : "完成第 1 步理解原片后，才能生成故事与英文解说。"
                                        color: storyDone ? "#8ee3b4" : textMuted
                                        font.pixelSize: 12
                                    }
                                }
                                FlatButton {
                                    text: appController.storyBusy ? "正在组织故事…" : storyDone ? "重新生成故事" : understandingDone ? "生成故事与英文解说  →" : "等待理解原片"
                                    enabled: understandingDone && !appController.storyBusy
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

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                enabled: understandingDone && !appController.storyBusy
                                opacity: enabled ? 1 : 0.45
                                Text { text: "目标上限"; color: textMuted; font.pixelSize: 11; Layout.rightMargin: 4 }
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
                                Text { text: "原片信息不足时会自动缩短，不会强行填满。"; color: "#737986"; font.pixelSize: 10; Layout.fillWidth: true }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                visible: appController.storyBusy || appController.storyStatus.indexOf("故事生成失败") === 0
                                Layout.preferredHeight: visible ? 62 : 0
                                radius: 10
                                color: "#15171e"
                                border.color: appController.storyStatus.indexOf("故事生成失败") === 0 ? "#7f3d46" : "#292c36"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 13
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 7
                                        Text { text: appController.storyStatus; color: textMain; font.pixelSize: 12 }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 5
                                            radius: 3
                                            color: "#30333d"
                                            Rectangle { width: parent.width * appController.storyProgress; height: parent.height; radius: 3; color: accent }
                                        }
                                    }
                                    Text { text: Math.round(appController.storyProgress * 100) + "%"; color: accentLight; font.pixelSize: 12; font.bold: true }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                visible: storyDone

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

                                Text { text: "故事大纲"; color: textMain; font.pixelSize: 15; font.bold: true }
                                Repeater {
                                    model: appController.storyOutline
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 52
                                        radius: 10
                                        color: "#15171e"
                                        border.color: "#292c36"
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 12; spacing: 12
                                            Text { text: modelData.order; color: accentLight; font.pixelSize: 13; font.bold: true }
                                            Text { text: modelData.purpose; color: "#a1a6b2"; font.pixelSize: 11; Layout.preferredWidth: 70 }
                                            Text { text: modelData.summary; color: textMain; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                                            Text { text: "事件 " + modelData.event_ids.join(", "); color: textMuted; font.pixelSize: 10 }
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
                        Layout.fillWidth: true
                        Layout.preferredHeight: matchingContent.implicitHeight + 36
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
                                    Text { text: "匹配镜头"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: appController.matches.length > 0
                                              ? appController.matchingStatus
                                              : appController.storyNarration.length > 0
                                                ? "按故事事件绑定和画面描述，为每句解说提供最多 5 个原片候选。"
                                                : "完成故事与英文解说后，才能开始镜头匹配。"
                                        color: textMuted; font.pixelSize: 12
                                    }
                                }
                                FlatButton {
                                    text: appController.matchingBusy ? "正在匹配…" : appController.matches.length > 0 ? "重新匹配" : "生成镜头匹配  →"
                                    enabled: appController.storyNarration.length > 0 && !appController.matchingBusy
                                    onClicked: appController.generateMatches()
                                }
                            }
                            Text {
                                visible: appController.matches.length > 0
                                text: appController.roughCutSummary
                                color: accentLight
                                font.pixelSize: 11
                            }

                            Repeater {
                                model: appController.matches
                                delegate: Rectangle {
                                    id: matchingItem
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 292
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
                        Layout.fillWidth: true
                        Layout.preferredHeight: appController.matches.length > 0 ? 260 : 116
                        radius: 14
                        color: panel
                        border.color: exportDone ? "#326b4d" : "#292c36"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 12
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16
                                StepBadge { stepNumber: "04"; completed: exportDone }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Text { text: "预览导出"; color: textMain; font.pixelSize: 18; font.bold: true }
                                    Text {
                                        text: appController.matches.length > 0
                                              ? "使用 GPT-SoVITS 英文解说；保持原视频分辨率和画面，不缩放、不补边、不裁切。"
                                              : "完成镜头匹配后，才能生成粗剪预览。"
                                        color: textMuted; font.pixelSize: 12
                                    }
                                }
                                FlatButton {
                                    text: appController.exportBusy ? "正在生成…" : appController.previewVideoReady ? "重新生成成片预览" : "生成成片预览  →"
                                    enabled: appController.narrationAudioReady && !appController.exportBusy
                                    onClicked: appController.generateRoughPreview()
                                }
                                GhostButton {
                                    text: "字幕样式"
                                    enabled: appController.syncedSrtReady
                                    onClicked: subtitleStyleDialog.open()
                                }
                                GhostButton {
                                    visible: appController.previewVideoReady
                                    text: "用播放器打开"
                                    onClicked: appController.openRoughPreview()
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: appController.matches.length > 0
                                spacing: 9
                                GhostButton {
                                    text: "1  导出 SRT 到 GPT-SoVITS"
                                    onClicked: saveTtsSrtDialog.open()
                                }
                                GhostButton {
                                    text: appController.narrationAudioReady ? "重新导入英文配音" : "2  导入英文配音"
                                    onClicked: narrationAudioDialog.open()
                                }
                                GhostButton {
                                    text: appController.syncedSrtReady ? "重新导入同步 SRT" : "3  导入同步 SRT（可选）"
                                    onClicked: narrationSrtDialog.open()
                                }
                                Text {
                                    text: appController.voiceStatus
                                    color: appController.narrationAudioReady ? "#63d39a" : textMuted
                                    font.pixelSize: 10
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    visible: appController.narrationAudioReady
                                    text: "配音 " + appController.narrationDurationText
                                    color: accentLight
                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: appController.matches.length > 0
                                spacing: 7
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: appController.exportStatus; color: appController.previewVideoReady ? "#63d39a" : textMain; font.pixelSize: 11; Layout.fillWidth: true }
                                    Text { text: Math.round(appController.exportProgress * 100) + "%"; color: accentLight; font.pixelSize: 11; font.bold: true }
                                }
                                Rectangle {
                                    Layout.fillWidth: true; height: 5; radius: 3; color: "#30333d"
                                    Rectangle { width: parent.width * appController.exportProgress; height: parent.height; radius: 3; color: accent }
                                }
                                Text { visible: appController.previewVideoReady; text: appController.previewVideoPath; color: textMuted; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideMiddle }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 8
                        visible: appController.events.length > 0
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
                        visible: appController.events.length > 0

                        Repeater {
                            model: appController.events.slice(0, 12)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 136
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
                                        Text { Layout.fillWidth: true; Layout.fillHeight: true; text: modelData.transcript || "（该场景没有对白）"; color: textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight }
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
