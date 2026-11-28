pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.VirtualKeyboard.Components

KeyboardRow {
    objectName: "deckNumberRow"
    Repeater {
        model: "1234567890".split("")
        Key {
            required property string modelData
            objectName: "deckDigit" + modelData
            key: Qt.Key_0 + Number(modelData)
            text: modelData
            noModifier: true
        }
    }
}
