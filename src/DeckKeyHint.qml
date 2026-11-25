import QtQuick

Text {
    objectName: "deckKeyHint"
    enabled: false
    z: 3
    anchors.horizontalCenter: parent.horizontalCenter
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Math.max(2, Math.round(parent.height * 0.07))
    text: parent.smallText
    textFormat: Text.PlainText
    font.pixelSize: Math.max(8, Math.min(13, Math.round(parent.height * 0.21)))
    font.weight: Font.Medium
    property color hintColor: colors.text
    color: hintColor
    opacity: 0.74
    SystemPalette { id: colors; colorGroup: SystemPalette.Active }
}
