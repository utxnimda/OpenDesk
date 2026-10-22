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
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum PetMode {
    Desktop,
    InApp,
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
            r#"{"pet":null}"#,
        ] {
            assert!(serde_json::from_str::<Personalization>(input).is_err());
        }
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
