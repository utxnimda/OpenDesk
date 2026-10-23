use serde::{de::IgnoredAny, Deserialize, Deserializer, Serialize};

#[derive(Debug, Clone, Default, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Personalization {
    pub pet: PetSettings,
}

impl<'de> Deserialize<'de> for Personalization {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        // Schema 1 previously stored these two retired features. Consume their
        // values without retaining paths, decoding assets, or rewriting the file.
        #[derive(Default, Deserialize)]
        #[serde(default, rename_all = "camelCase", deny_unknown_fields)]
        struct Wire {
            pet: PetSettings,
            #[serde(rename = "background")]
            _background: Option<IgnoredAny>,
            #[serde(rename = "effects")]
            _effects: Option<IgnoredAny>,
        }
        let wire = Wire::deserialize(deserializer)?;
        Ok(Self { pet: wire.pet })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", default, deny_unknown_fields)]
pub struct PetSettings {
    pub enabled: bool,
    pub mode: PetMode,
    pub model_id: Option<String>,
    pub size: u16,
    pub always_on_top: bool,
    pub animated: bool,
    #[serde(default)]
    pub action_mode: PetActionMode,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum PetMode {
    Desktop,
    InApp,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum PetActionMode {
    #[default]
    Idle,
    Walk,
    Wave,
    Sleep,
    Thinking,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(
    tag = "type",
    content = "value",
    rename_all = "kebab-case",
    deny_unknown_fields
)]
pub enum CompanionChange {
    Mode(PetMode),
    Size(u16),
    AlwaysOnTop(bool),
    Animated(bool),
    ActionMode(PetActionMode),
}

impl CompanionChange {
    pub fn apply(self, pet: &mut PetSettings) {
        match self {
            Self::Mode(value) => pet.mode = value,
            Self::Size(value) => pet.size = value,
            Self::AlwaysOnTop(value) => pet.always_on_top = value,
            Self::Animated(value) => pet.animated = value,
            Self::ActionMode(value) => pet.action_mode = value,
        }
    }
}

impl Default for PetSettings {
    fn default() -> Self {
        Self {
            enabled: false,
            mode: PetMode::Desktop,
            model_id: None,
            size: 160,
            always_on_top: true,
            animated: true,
            action_mode: PetActionMode::Idle,
        }
    }
}

impl Personalization {
    pub fn validate(&self) -> Result<(), String> {
        if !(96..=256).contains(&self.pet.size) {
            return Err("Companion size must be between 96 and 256.".into());
        }
        if self.pet.model_id.as_deref().is_some_and(|id| {
            id.is_empty()
                || id.len() > 64
                || !id
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        }) {
            return Err("Invalid companion model identity.".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_companion_size_and_model_identity() {
        let mut value = Personalization::default();
        assert!(value.validate().is_ok());
        for size in [0, 95, 257, u16::MAX] {
            value.pet.size = size;
            assert!(value.validate().is_err());
        }
        for size in [96, 160, 256] {
            value.pet.size = size;
            assert!(value.validate().is_ok());
        }
        value.pet.model_id = Some("sample-companion".into());
        assert!(value.validate().is_ok());
        for id in [
            "",
            "../duck",
            "C:/pet.png",
            "https://example.com/pet",
            "Duck",
        ] {
            value.pet.model_id = Some(id.into());
            assert!(value.validate().is_err());
        }
        value.pet.model_id = Some("a".repeat(65));
        assert!(value.validate().is_err());
    }

    #[test]
    fn missing_fields_default_but_unknown_fields_and_enums_are_rejected() {
        assert_eq!(
            serde_json::from_str::<Personalization>("{}").unwrap(),
            Personalization::default()
        );
        for input in [
            r#"{"unknownFeature":{}}"#,
            r#"{"pet":{"mode":"external-process"}}"#,
            r#"{"pet":{"path":"C:/pet.png"}}"#,
            r#"{"pet":{"enabled":"yes"}}"#,
            r#"{"pet":{"actionMode":"future-action"}}"#,
            r#"{"pet":null}"#,
        ] {
            assert!(serde_json::from_str::<Personalization>(input).is_err());
        }
    }

    #[test]
    fn action_mode_defaults_for_legacy_preferences_and_round_trips() {
        let legacy: PetSettings =
            serde_json::from_str(r#"{"modelId":"sample-companion","size":208}"#).unwrap();
        assert_eq!(legacy.action_mode, PetActionMode::Idle);
        for (mode, name) in [
            (PetActionMode::Idle, "idle"),
            (PetActionMode::Walk, "walk"),
            (PetActionMode::Wave, "wave"),
            (PetActionMode::Sleep, "sleep"),
            (PetActionMode::Thinking, "thinking"),
        ] {
            let settings = PetSettings {
                action_mode: mode,
                ..legacy.clone()
            };
            let serialized = serde_json::to_value(&settings).unwrap();
            assert_eq!(serialized["actionMode"], name);
            assert_eq!(
                serde_json::from_value::<PetSettings>(serialized).unwrap(),
                settings
            );
        }
    }

    #[test]
    fn companion_changes_reject_unknown_fields_types_and_actions() {
        for input in [
            r#"{"type":"model-id","value":"another"}"#,
            r#"{"type":"enabled","value":true}"#,
            r#"{"type":"size","value":160,"enabled":true}"#,
            r#"{"type":"size","value":160,"modelId":"another"}"#,
            r#"{"type":"animated","value":"true"}"#,
            r#"{"type":"always-on-top","value":1}"#,
            r#"{"type":"mode","value":"external"}"#,
            r#"{"type":"action-mode","value":"future-action"}"#,
            r#"{"type":"size","value":96.5}"#,
            r#"{"type":"size","value":-1}"#,
            r#"{"type":"size"}"#,
        ] {
            assert!(
                serde_json::from_str::<CompanionChange>(input).is_err(),
                "must reject {input}"
            );
        }
        assert_eq!(
            serde_json::from_str::<CompanionChange>(r#"{"type":"action-mode","value":"thinking"}"#)
                .unwrap(),
            CompanionChange::ActionMode(PetActionMode::Thinking)
        );
    }

    #[test]
    fn retired_fields_are_consumed_but_never_serialized() {
        let value: Personalization = serde_json::from_str(
            r#"{"background":{"assetId":"unavailable-old-image","blur":999},"effects":{"motion":"old-motion"},"pet":{"enabled":true,"mode":"in-app"}}"#,
        )
        .unwrap();
        assert!(value.validate().is_ok());
        assert!(value.pet.enabled);
        assert_eq!(value.pet.mode, PetMode::InApp);
        let serialized = serde_json::to_value(&value).unwrap();
        assert_eq!(serialized.as_object().unwrap().len(), 1);
        assert!(serialized.get("pet").is_some());
        assert_eq!(
            serde_json::from_str::<Personalization>(r#"{"background":null,"effects":[1,2]}"#)
                .unwrap(),
            Personalization::default()
        );
    }
}
